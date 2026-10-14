import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Config:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 8000))
    
    # Audio settings
    SAMPLE_RATE = 16000  # 16 kHz standard for Whisper and VAD
    CHANNELS = 1         # Mono
    CHUNK_SIZE = 1024    # Frame chunk size
    
    # Edge-TTS Voice options: en-US-ChristopherNeural, en-US-JennyNeural, en-US-GuyNeural
    TTS_VOICE = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")
    
    # Gemini Model (Free tier supports gemini-2.0-flash / gemini-1.5-flash)
    GEMINI_MODEL = "gemini-2.0-flash"

config = Config()
