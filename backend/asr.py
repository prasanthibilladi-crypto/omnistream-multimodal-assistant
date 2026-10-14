import io
import wave
import logging
from typing import Optional
import speech_recognition as sr
from backend.config import config

logger = logging.getLogger(__name__)

class SpeechRecognizer:
    """
    Sub-second Speech-to-Text Transcriber.
    Uses Groq's Whisper if GROQ_API_KEY is available, 
    otherwise falls back to free Google Speech Recognition with 0 API keys required.
    """
    def __init__(self):
        self.groq_client = None
        self.sr_recognizer = sr.Recognizer()
        
        if config.GROQ_API_KEY:
            try:
                from groq import Groq
                self.groq_client = Groq(api_key=config.GROQ_API_KEY)
                logger.info("Groq Whisper ASR initialized successfully.")
            except Exception as e:
                logger.warning(f"Failed to initialize Groq client: {e}")
        else:
            logger.info("Using free SpeechRecognition engine (0 keys required).")

    def _pcm_to_wav_io(self, pcm_bytes: bytes, sample_rate: int = 16000) -> io.BytesIO:
        """Converts raw 16-bit PCM bytes to standard in-memory WAV BytesIO."""
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wav_file:
            wav_file.setnchannels(1)        # Mono
            wav_file.setsampwidth(2)       # 16-bit = 2 bytes
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)
        wav_io.seek(0)
        return wav_io

    async def transcribe(self, pcm_bytes: bytes) -> Optional[str]:
        """Transcribes PCM audio bytes into text."""
        if not pcm_bytes or len(pcm_bytes) < 3200: # Less than 100ms
            return None

        wav_io = self._pcm_to_wav_io(pcm_bytes, config.SAMPLE_RATE)
        
        # 1. Try Groq Whisper if key is provided
        if self.groq_client:
            try:
                transcription = self.groq_client.audio.transcriptions.create(
                    file=("audio.wav", wav_io.getvalue()),
                    model="whisper-large-v3",
                    response_format="text",
                    temperature=0.0
                )
                text = str(transcription).strip()
                if text:
                    return text
            except Exception as e:
                logger.error(f"Error during Groq ASR transcription: {e}")

        # 2. 100% Free Fallback: Use speech_recognition engine (no keys needed)
        try:
            wav_io.seek(0)
            with sr.AudioFile(wav_io) as source:
                audio_data = self.sr_recognizer.record(source)
                recognized_text = self.sr_recognizer.recognize_google(audio_data)
                logger.info(f"Speech recognized by backend: {recognized_text}")
                return recognized_text.strip()
        except sr.UnknownValueError:
            logger.info("Speech was unintelligible or silence.")
            return None
        except sr.RequestError as e:
            logger.warning(f"Speech recognition service error: {e}")
            return None
        except Exception as e:
            logger.error(f"Error in backend speech recognition: {e}")
            return None
