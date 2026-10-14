import asyncio
import logging
from typing import Optional
import edge_tts
from backend.config import config

logger = logging.getLogger(__name__)

class StreamingTTS:
    """
    Zero-Cost Streaming Neural Text-to-Speech Engine.
    Generates complete, valid MP3 audio buffers per sentence to ensure 
    browser Web Audio / HTML5 audio decoders play seamlessly without corruption.
    """
    def __init__(self, voice: str = config.TTS_VOICE):
        self.voice = voice

    async def generate_sentence_audio(self, text: str) -> Optional[bytes]:
        """
        Synthesizes a complete sentence into a valid MP3 buffer in-memory.
        """
        if not text or not text.strip():
            return None

        clean_text = text.strip()
        try:
            communicate = edge_tts.Communicate(clean_text, self.voice)
            chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            
            if chunks:
                return b"".join(chunks)
            return None
        except asyncio.CancelledError:
            logger.info("TTS generation cancelled.")
            raise
        except Exception as e:
            logger.error(f"Error generating TTS audio for '{clean_text}': {e}")
            return None
