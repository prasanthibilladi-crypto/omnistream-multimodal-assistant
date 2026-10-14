import time
import json
import asyncio
import logging
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import config
from backend.vad import VoiceActivityDetector
from backend.asr import SpeechRecognizer
from backend.llm import MultimodalEngine
from backend.tts import StreamingTTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Real-Time Multimodal Voice & Vision Assistant")

# Mount frontend directory for static assets
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
async def get_index():
    response = FileResponse(FRONTEND_DIR / "index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.websocket("/ws/stream")
async def websocket_stream_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Client connected to real-time streaming pipeline.")

    # Initialize per-session components
    vad = VoiceActivityDetector(sample_rate=config.SAMPLE_RATE)
    asr = SpeechRecognizer()
    llm = MultimodalEngine()
    tts = StreamingTTS()

    latest_video_frame = None
    current_ai_task: asyncio.Task = None
    last_transcription_text = ""
    last_transcription_time = 0

    async def cancel_ongoing_generation():
        nonlocal current_ai_task
        if current_ai_task and not current_ai_task.done():
            logger.info("Cancelling in-flight generation on user request.")
            current_ai_task.cancel()
            try:
                await current_ai_task
            except asyncio.CancelledError:
                pass
            current_ai_task = None
            try:
                await websocket.send_json({"type": "interrupt"})
            except Exception:
                pass

    def is_duplicate_transcription(text: str) -> bool:
        nonlocal last_transcription_text, last_transcription_time
        now = time.time()
        # If identical text is received within 3 seconds, treat as duplicate and ignore
        if text.strip().lower() == last_transcription_text.strip().lower() and (now - last_transcription_time) < 3.0:
            return True
        last_transcription_text = text
        last_transcription_time = now
        return False

    async def run_ai_pipeline(transcribed_text: str, video_frame: str):
        try:
            start_time = time.time()
            first_token_time = None
            first_audio_time = None
            accumulated_response = ""

            await websocket.send_json({"type": "ai_start", "prompt": transcribed_text})

            # Stream tokens from Multimodal LLM
            sentence_buffer = ""
            async for token in llm.stream_response(transcribed_text, video_frame):
                if first_token_time is None:
                    first_token_time = time.time()
                    ttft_ms = int((first_token_time - start_time) * 1000)
                    await websocket.send_json({"type": "metric_ttft", "value_ms": ttft_ms})

                accumulated_response += token
                sentence_buffer += token

                await websocket.send_json({"type": "ai_text_chunk", "chunk": token})

                # Once a natural clause/sentence finishes, generate and send complete audio clip
                if any(punct in sentence_buffer for punct in [".", "?", "!", "\n"]):
                    text_to_speak = sentence_buffer.strip()
                    sentence_buffer = ""
                    if text_to_speak and len(text_to_speak) > 2:
                        audio_bytes = await tts.generate_sentence_audio(text_to_speak)
                        if audio_bytes:
                            if first_audio_time is None:
                                first_audio_time = time.time()
                                ttfb_ms = int((first_audio_time - start_time) * 1000)
                                await websocket.send_json({"type": "metric_ttfb", "value_ms": ttfb_ms})

                            # Send complete, valid MP3 clip for this sentence
                            await websocket.send_bytes(audio_bytes)

            # Flush remaining sentence buffer
            if sentence_buffer.strip() and len(sentence_buffer.strip()) > 2:
                audio_bytes = await tts.generate_sentence_audio(sentence_buffer.strip())
                if audio_bytes:
                    await websocket.send_bytes(audio_bytes)

            total_latency_ms = int((time.time() - start_time) * 1000)
            await websocket.send_json({
                "type": "ai_complete",
                "full_text": accumulated_response.strip(),
                "total_latency_ms": total_latency_ms
            })

        except asyncio.CancelledError:
            logger.info("AI generation pipeline cancelled.")
        except Exception as e:
            logger.error(f"Error during AI pipeline execution: {e}")
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message and message["bytes"]:
                # Incoming PCM audio frame
                audio_bytes = message["bytes"]
                is_speaking, has_ended, completed_audio = vad.process_chunk(audio_bytes)

                # Only transcribe if AI is NOT currently speaking (prevents self-cancellation & echo loops)
                is_ai_busy = current_ai_task and not current_ai_task.done()

                if has_ended and completed_audio and not is_ai_busy:
                    logger.info("Speech end detected by VAD. Transcribing...")
                    await websocket.send_json({"type": "status", "status": "Transcribing audio..."})
                    
                    transcription = await asr.transcribe(completed_audio)
                    if transcription and not is_duplicate_transcription(transcription):
                        logger.info(f"User transcription: {transcription}")
                        await websocket.send_json({"type": "user_transcription", "text": transcription})
                        
                        current_ai_task = asyncio.create_task(
                            run_ai_pipeline(transcription, latest_video_frame)
                        )
                    else:
                        await websocket.send_json({"type": "status", "status": "Listening..."})

            elif "text" in message and message["text"]:
                data = json.loads(message["text"])
                msg_type = data.get("type")

                if msg_type == "video_frame":
                    latest_video_frame = data.get("image")

                elif msg_type == "interrupt":
                    await cancel_ongoing_generation()

                elif msg_type == "speech_recognized":
                    user_text = data.get("text", "").strip()
                    if user_text and not is_duplicate_transcription(user_text):
                        logger.info(f"Browser Web Speech recognized: {user_text}")
                        await cancel_ongoing_generation()
                        await websocket.send_json({"type": "user_transcription", "text": user_text})
                        current_ai_task = asyncio.create_task(
                            run_ai_pipeline(user_text, latest_video_frame)
                        )

                elif msg_type == "manual_text":
                    user_text = data.get("text", "").strip()
                    if user_text:
                        await cancel_ongoing_generation()
                        await websocket.send_json({"type": "user_transcription", "text": user_text})
                        current_ai_task = asyncio.create_task(
                            run_ai_pipeline(user_text, latest_video_frame)
                        )

                elif msg_type == "set_keys":
                    gemini_key = data.get("gemini_key", "").strip()
                    if gemini_key:
                        llm.set_api_key(gemini_key)
                        await websocket.send_json({"type": "status", "status": "Gemini API Key activated!"})

    except WebSocketDisconnect:
        logger.info("Client disconnected from WebSocket.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if current_ai_task and not current_ai_task.done():
            current_ai_task.cancel()
