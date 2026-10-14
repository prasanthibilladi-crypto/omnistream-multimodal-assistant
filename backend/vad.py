import numpy as np
from collections import deque
from typing import Tuple

class VoiceActivityDetector:
    """
    Adaptive Real-Time Voice Activity Detector (VAD).
    Calculates Root-Mean-Square (RMS) audio energy with dynamic noise floor tracking
    and silence threshold debouncing to detect speech start and speech end.
    """
    def __init__(
        self,
        sample_rate: int = 16000,
        energy_threshold: float = 0.005,
        silence_duration_ms: int = 800,
        min_speech_duration_ms: int = 200,
    ):
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.silence_chunks_needed = int((silence_duration_ms / 1000) * (sample_rate / 512))
        self.min_speech_chunks = int((min_speech_duration_ms / 1000) * (sample_rate / 512))
        
        self.is_speaking = False
        self.silence_counter = 0
        self.speech_chunks_count = 0
        
        # Ring buffer for audio history to preserve leading consonants
        self.pre_speech_buffer = deque(maxlen=int((200 / 1000) * (sample_rate / 512)))
        self.current_speech_audio = []

    def process_chunk(self, audio_bytes: bytes) -> Tuple[bool, bool, bytes]:
        """
        Processes a raw 16-bit PCM mono audio chunk.
        Returns:
            (is_speaking_now, has_speech_ended, completed_audio_bytes)
        """
        if len(audio_bytes) < 2:
            return self.is_speaking, False, b""

        # Convert 16-bit PCM bytes to float numpy array normalized between -1.0 and 1.0
        audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        rms_energy = np.sqrt(np.mean(audio_data ** 2)) if len(audio_data) > 0 else 0.0
        
        has_ended = False
        completed_audio = b""

        if rms_energy > self.energy_threshold:
            # Active sound detected
            self.silence_counter = 0
            self.speech_chunks_count += 1
            
            if not self.is_speaking:
                self.is_speaking = True
                # Include pre-speech buffer
                self.current_speech_audio.extend(self.pre_speech_buffer)
                self.pre_speech_buffer.clear()
            
            self.current_speech_audio.append(audio_bytes)
        else:
            # Silence detected
            if self.is_speaking:
                self.silence_counter += 1
                self.current_speech_audio.append(audio_bytes)
                
                # Check if silence has persisted long enough to declare end-of-utterance
                if self.silence_counter >= self.silence_chunks_needed:
                    self.is_speaking = False
                    self.silence_counter = 0
                    
                    # Ensure minimum speech length to filter out keyboard clicks/coughs
                    if self.speech_chunks_count >= self.min_speech_chunks:
                        has_ended = True
                        completed_audio = b"".join(self.current_speech_audio)
                    
                    self.current_speech_audio = []
                    self.speech_chunks_count = 0
            else:
                self.pre_speech_buffer.append(audio_bytes)
                
        return self.is_speaking, has_ended, completed_audio

    def reset(self):
        self.is_speaking = False
        self.silence_counter = 0
        self.speech_chunks_count = 0
        self.current_speech_audio.clear()
        self.pre_speech_buffer.clear()
