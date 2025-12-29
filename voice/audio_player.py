"""
Discord Audio Player
Plays AI voice responses to Discord voice channel
"""

import asyncio
import io
import subprocess
import tempfile
import os
import sys
from typing import Optional, Any
from collections import deque

import discord

# Get FFmpeg path (local or system)
def get_ffmpeg_path() -> str:
    """Get path to FFmpeg executable"""
    # Check for local ffmpeg.exe in project root
    local_ffmpeg = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ffmpeg.exe')
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg
    # Fall back to system PATH
    return 'ffmpeg'

FFMPEG_PATH = get_ffmpeg_path()

from .audio_utils import AudioProcessor


class VoicePlayer:
    """
    Plays audio responses in Discord voice channel

    Handles:
    - Converting Gemini audio (24kHz) to Discord format (48kHz)
    - Queuing audio chunks for smooth playback
    - Interruption handling (stop when user speaks)
    """

    def __init__(self, voice_client: discord.VoiceClient):
        """
        Initialize the voice player

        Args:
            voice_client: Discord VoiceClient connected to a channel
        """
        self.vc = voice_client
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self._is_playing = False
        self._stop_requested = False
        self._current_source: Optional[discord.AudioSource] = None
        self._playback_task: Optional[asyncio.Task] = None

        # Buffer for accumulating audio chunks
        self._audio_buffer = bytearray()
        self._min_buffer_size = 24000 * 2  # ~0.5 sec at 24kHz mono 16-bit

    async def start(self) -> None:
        """Start the playback processor"""
        if self._playback_task is None or self._playback_task.done():
            self._playback_task = asyncio.create_task(self._playback_loop())

    async def stop(self) -> None:
        """Stop playback and clear queue"""
        self._stop_requested = True

        # Stop current playback
        if self.vc.is_playing():
            self.vc.stop()

        # Clear the queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Clear buffer
        self._audio_buffer.clear()

        self._is_playing = False
        self._stop_requested = False

    async def queue_audio(self, audio_bytes: bytes) -> None:
        """
        Queue audio bytes for playback

        Args:
            audio_bytes: PCM audio from Gemini (24kHz, mono, 16-bit)
        """
        if not audio_bytes:
            return

        await self._audio_queue.put(audio_bytes)

        # Ensure playback loop is running
        await self.start()

    async def play_immediate(self, audio_bytes: bytes) -> None:
        """
        Play audio immediately, interrupting current playback

        Args:
            audio_bytes: PCM audio from Gemini (24kHz, mono, 16-bit)
        """
        await self.stop()
        await self.queue_audio(audio_bytes)

    async def _playback_loop(self) -> None:
        """Background loop that processes audio queue"""
        try:
            while not self._stop_requested:
                # Wait for audio with timeout
                try:
                    audio_chunk = await asyncio.wait_for(
                        self._audio_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    # No audio available, check if we have buffered audio to play
                    if self._audio_buffer and len(self._audio_buffer) >= self._min_buffer_size:
                        await self._play_buffer()
                    continue

                if self._stop_requested:
                    break

                # Add to buffer
                self._audio_buffer.extend(audio_chunk)

                # Play when buffer is large enough
                if len(self._audio_buffer) >= self._min_buffer_size:
                    await self._play_buffer()

            # Play any remaining buffered audio
            if self._audio_buffer:
                await self._play_buffer()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[VoicePlayer] Playback error: {e}")
        finally:
            self._is_playing = False

    async def _play_buffer(self) -> None:
        """Play the accumulated audio buffer"""
        if not self._audio_buffer or self._stop_requested:
            return

        if not self.vc or not self.vc.is_connected():
            self._audio_buffer.clear()
            return

        try:
            # Get buffer contents and clear
            audio_data = bytes(self._audio_buffer)
            self._audio_buffer.clear()

            # Convert Gemini audio (24kHz mono) to Discord format (48kHz stereo)
            discord_audio = AudioProcessor.gemini_to_discord(audio_data)

            # Create audio source using FFmpeg for proper Discord encoding
            source = await self._create_audio_source(discord_audio)

            if source and not self._stop_requested:
                self._is_playing = True

                # Wait for current playback to finish
                while self.vc.is_playing():
                    if self._stop_requested:
                        self.vc.stop()
                        return
                    await asyncio.sleep(0.05)

                # Play the audio
                self.vc.play(source, after=lambda e: self._on_playback_done(e))

                # Wait for playback to complete
                while self.vc.is_playing():
                    if self._stop_requested:
                        self.vc.stop()
                        break
                    await asyncio.sleep(0.05)

        except Exception as e:
            print(f"[VoicePlayer] Buffer play error: {e}")

        self._is_playing = False

    async def _create_audio_source(self, pcm_data: bytes) -> Optional[discord.AudioSource]:
        """
        Create Discord AudioSource from PCM data

        Args:
            pcm_data: Raw PCM bytes (48kHz, stereo, 16-bit)

        Returns:
            Discord AudioSource ready to play
        """
        try:
            # Method 1: Use FFmpeg with pipe input
            # This is the most reliable method for Discord

            # Create a temporary file for the audio
            with tempfile.NamedTemporaryFile(suffix='.pcm', delete=False) as f:
                f.write(pcm_data)
                temp_path = f.name

            try:
                # Create FFmpeg audio source
                source = discord.FFmpegPCMAudio(
                    temp_path,
                    before_options='-f s16le -ar 48000 -ac 2',
                    executable=FFMPEG_PATH,
                )
                return source
            finally:
                # Schedule cleanup of temp file
                asyncio.get_event_loop().call_later(
                    5.0,
                    lambda: self._cleanup_temp_file(temp_path)
                )

        except Exception as e:
            print(f"[VoicePlayer] Audio source error: {e}")
            return None

    def _cleanup_temp_file(self, path: str) -> None:
        """Remove temporary audio file"""
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass

    def _on_playback_done(self, error: Optional[Exception]) -> None:
        """Callback when playback completes"""
        if error:
            print(f"[VoicePlayer] Playback error: {error}")
        self._is_playing = False

    @property
    def is_playing(self) -> bool:
        """Check if currently playing audio"""
        return self._is_playing or self.vc.is_playing()

    async def cleanup(self) -> None:
        """Cleanup resources"""
        await self.stop()

        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass


class PCMAudioSource(discord.AudioSource):
    """Simple PCM audio source for Discord"""

    def __init__(self, pcm_data: bytes, *, sample_rate: int = 48000, channels: int = 2):
        self._data = io.BytesIO(pcm_data)
        self._sample_rate = sample_rate
        self._channels = channels
        # Discord expects 20ms frames at 48kHz stereo = 3840 bytes
        self._frame_size = int(sample_rate * channels * 2 * 0.02)

    def read(self) -> bytes:
        """Read a frame of audio"""
        data = self._data.read(self._frame_size)
        if len(data) < self._frame_size:
            # Pad with silence if needed
            data += b'\x00' * (self._frame_size - len(data))
        return data if data else b''

    def is_opus(self) -> bool:
        """We're sending PCM, not Opus"""
        return False

    def cleanup(self) -> None:
        """Cleanup"""
        self._data.close()
