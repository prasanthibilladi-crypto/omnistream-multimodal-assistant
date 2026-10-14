import io
import base64
import logging
from typing import AsyncGenerator, Optional, List, Dict
from PIL import Image
from backend.config import config

logger = logging.getLogger(__name__)

class MultimodalEngine:
    """
    Multimodal Vision-Language Streaming Engine.
    Uses Google Gemini (Free Tier) to process user queries and webcam snapshots with streaming token output.
    """
    def __init__(self):
        self.client = None
        self.chat_history: List[Dict[str, str]] = []
        
        if config.GEMINI_API_KEY:
            try:
                from google import genai
                self.client = genai.Client(api_key=config.GEMINI_API_KEY)
                logger.info("Google Gemini Multimodal Engine initialized.")
            except Exception as e:
                logger.warning(f"Could not initialize google-genai client: {e}")
        else:
            logger.warning("GEMINI_API_KEY not found. Running in demo mode.")

    def _decode_image(self, base64_str: str) -> Optional[Image.Image]:
        """Decodes base64 string from browser canvas into a PIL Image."""
        try:
            if "," in base64_str:
                base64_str = base64_str.split(",")[1]
            image_data = base64.b64decode(base64_str)
            return Image.open(io.BytesIO(image_data))
        except Exception as e:
            logger.error(f"Error decoding image frame: {e}")
            return None

    def set_api_key(self, api_key: str):
        if api_key:
            config.GEMINI_API_KEY = api_key
            try:
                from google import genai
                self.client = genai.Client(api_key=api_key)
                logger.info("Updated Google Gemini API key.")
                return True
            except Exception as e:
                logger.error(f"Error initializing Gemini with provided key: {e}")
        return False

    async def stream_response(
        self,
        prompt: str,
        image_base64: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        Streams response tokens asynchronously.
        If an image frame is provided, it is included in the multimodal prompt.
        """
        system_instruction = (
            "You are OmniStream, a real-time conversational AI with live vision and hearing. "
            "Speak conversationally, concisely, and directly in 1 to 2 punchy sentences. "
            "Never use markdown asterisks or bullet points since your text is converted directly to speech."
        )

        pil_image = None
        if image_base64:
            pil_image = self._decode_image(image_base64)

        if self.client:
            try:
                contents = []
                if pil_image:
                    contents.append(pil_image)
                contents.append(prompt)

                response = self.client.models.generate_content_stream(
                    model=config.GEMINI_MODEL,
                    contents=contents,
                    config={
                        "system_instruction": system_instruction,
                        "temperature": 0.7,
                        "max_output_tokens": 150,
                    }
                )

                for chunk in response:
                    if chunk.text:
                        yield chunk.text

            except Exception as e:
                logger.error(f"Gemini API streaming error: {e}")
                yield "I experienced a momentary connection glitch with Gemini."
        else:
            import asyncio
            has_camera = "and I can clearly see your webcam feed! " if pil_image else ""
            clean_prompt = prompt.strip()
            reply = (
                f"I heard you say: '{clean_prompt}'. {has_camera}"
                f"I am responding in offline demo mode. "
                f"To activate live frontier reasoning and visual analysis, add your free Gemini key in the API Key settings."
            )
            # Yield natural phrases with explicit spaces
            words = reply.split(" ")
            for i, word in enumerate(words):
                yield word + (" " if i < len(words) - 1 else "")
                await asyncio.sleep(0.04)
