"""
Audio Processing Utilities
Handles resampling between Discord (48kHz) and Gemini (16kHz/24kHz)
"""

import numpy as np
from scipy import signal
from typing import Optional
import struct


class AudioProcessor:
    """Handles audio format conversion between Discord and Gemini"""

    # Discord audio format
    DISCORD_SAMPLE_RATE = 48000
    DISCORD_CHANNELS = 2  # Stereo

    # Gemini Live API format
    GEMINI_INPUT_RATE = 16000   # What we send to Gemini
    GEMINI_OUTPUT_RATE = 24000  # What Gemini sends back
    GEMINI_CHANNELS = 1         # Mono

    # Audio chunk settings
    CHUNK_DURATION_MS = 20  # Discord sends 20ms chunks

    @staticmethod
    def discord_to_gemini(pcm_data: bytes) -> bytes:
        """
        Convert Discord audio (48kHz stereo) to Gemini format (16kHz mono)

        Args:
            pcm_data: Raw PCM bytes from Discord (48kHz, stereo, 16-bit)

        Returns:
            PCM bytes for Gemini (16kHz, mono, 16-bit)
        """
        if not pcm_data:
            return b''

        # Convert bytes to numpy array (16-bit signed integers)
        audio = np.frombuffer(pcm_data, dtype=np.int16)

        # Convert stereo to mono (average both channels)
        if len(audio) % 2 == 0:
            audio = audio.reshape(-1, 2)
            audio = audio.mean(axis=1).astype(np.int16)

        # Resample from 48kHz to 16kHz (factor of 3)
        # Using scipy's resample for quality
        num_samples = int(len(audio) * AudioProcessor.GEMINI_INPUT_RATE / AudioProcessor.DISCORD_SAMPLE_RATE)
        audio_resampled = signal.resample(audio, num_samples).astype(np.int16)

        return audio_resampled.tobytes()

    @staticmethod
    def gemini_to_discord(pcm_data: bytes) -> bytes:
        """
        Convert Gemini audio (24kHz mono) to Discord format (48kHz stereo)

        Args:
            pcm_data: Raw PCM bytes from Gemini (24kHz, mono, 16-bit)

        Returns:
            PCM bytes for Discord (48kHz, stereo, 16-bit)
        """
        if not pcm_data:
            return b''

        # Convert bytes to numpy array
        audio = np.frombuffer(pcm_data, dtype=np.int16)

        # Resample from 24kHz to 48kHz (factor of 2)
        num_samples = int(len(audio) * AudioProcessor.DISCORD_SAMPLE_RATE / AudioProcessor.GEMINI_OUTPUT_RATE)
        audio_resampled = signal.resample(audio, num_samples).astype(np.int16)

        # Convert mono to stereo (duplicate channel)
        audio_stereo = np.column_stack((audio_resampled, audio_resampled)).flatten()

        return audio_stereo.astype(np.int16).tobytes()

    @staticmethod
    def normalize_audio(pcm_data: bytes, target_db: float = -3.0) -> bytes:
        """
        Normalize audio volume to target dB level

        Args:
            pcm_data: Raw PCM bytes (16-bit)
            target_db: Target dB level (default -3.0 for headroom)

        Returns:
            Normalized PCM bytes
        """
        if not pcm_data:
            return b''

        audio = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)

        # Calculate current RMS level
        rms = np.sqrt(np.mean(audio ** 2))
        if rms == 0:
            return pcm_data

        # Calculate target RMS from dB
        target_rms = 32767 * (10 ** (target_db / 20))

        # Apply gain
        gain = target_rms / rms
        audio = audio * gain

        # Clip to prevent overflow
        audio = np.clip(audio, -32768, 32767)

        return audio.astype(np.int16).tobytes()

    @staticmethod
    def detect_silence(pcm_data: bytes, threshold_db: float = -40.0) -> bool:
        """
        Detect if audio chunk is silence

        Args:
            pcm_data: Raw PCM bytes (16-bit)
            threshold_db: Silence threshold in dB

        Returns:
            True if audio is below silence threshold
        """
        if not pcm_data:
            return True

        audio = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)

        # Calculate RMS
        rms = np.sqrt(np.mean(audio ** 2))
        if rms == 0:
            return True

        # Convert to dB
        db = 20 * np.log10(rms / 32767)

        return db < threshold_db

    @staticmethod
    def get_audio_level(pcm_data: bytes) -> float:
        """
        Get audio level as a value from 0.0 to 1.0

        Args:
            pcm_data: Raw PCM bytes (16-bit)

        Returns:
            Audio level (0.0 = silence, 1.0 = max)
        """
        if not pcm_data:
            return 0.0

        audio = np.frombuffer(pcm_data, dtype=np.int16)

        # Get peak amplitude
        peak = np.max(np.abs(audio))

        return min(1.0, peak / 32767)
