"""
Discord Audio Sink for py-cord
Receives audio from users in voice channel using py-cord's native Sinks
"""

import asyncio
import io
from typing import Callable, Optional, Dict, Any, Set
from collections import defaultdict
import time

import discord
from discord.sinks import Sink, AudioData


class KiaraAudioSink(Sink):
    """
    Custom Sink for py-cord that captures per-user audio in real-time

    This extends py-cord's Sink class to process audio as it comes in,
    rather than waiting until recording stops.

    Features:
    - Per-user audio streams
    - Real-time audio callback
    - Voice activity tracking
    """

    def __init__(
        self,
        on_audio_chunk: Optional[Callable[[int, bytes], Any]] = None,
        on_user_speaking: Optional[Callable[[int, discord.Member], Any]] = None,
        *args, **kwargs
    ):
        """
        Initialize the audio sink

        Args:
            on_audio_chunk: Callback(user_id, audio_bytes) for audio data (48kHz stereo PCM)
            on_user_speaking: Callback(user_id, member) when user starts speaking
        """
        super().__init__(*args, **kwargs)

        self.on_audio_chunk = on_audio_chunk
        self.on_user_speaking = on_user_speaking

        # Track active sessions (users currently in conversation)
        self.active_sessions: Set[int] = set()

        # Track users we've seen speaking
        self._known_speakers: Set[int] = set()

        # Voice activity tracking
        self._last_voice_time: Dict[int, float] = {}

        # Event loop reference
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start_session(self, user_id: int) -> None:
        """Mark a user as in active conversation"""
        self.active_sessions.add(user_id)

    def end_session(self, user_id: int) -> None:
        """Remove user from active conversation"""
        self.active_sessions.discard(user_id)
        self._last_voice_time.pop(user_id, None)

    def is_active(self, user_id: int) -> bool:
        """Check if user has an active session"""
        return user_id in self.active_sessions

    def write(self, data: bytes, user: int) -> None:
        """
        Called for each audio packet received from a user

        This is py-cord's Sink interface method.

        Args:
            data: Raw audio bytes (PCM)
            user: User ID (int, not User object in Sink)
        """
        # Get event loop
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    self._loop = asyncio.get_event_loop()
                except RuntimeError:
                    return

        # Update voice activity
        self._last_voice_time[user] = time.time()

        # Check if new speaker
        if user not in self._known_speakers:
            self._known_speakers.add(user)
            if self.on_user_speaking:
                self._schedule_callback(self.on_user_speaking, user, None)

        # If user is in active session, send audio
        if user in self.active_sessions and self.on_audio_chunk:
            self._schedule_callback(self.on_audio_chunk, user, data)

        # Also call parent write to store in audio_data
        super().write(data, user)

    def _schedule_callback(self, callback: Callable, *args) -> None:
        """Schedule async callback from sync context"""
        if self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._run_callback(callback, *args),
                    self._loop
                )
            except Exception as e:
                print(f"[AudioSink] Schedule error: {e}")

    async def _run_callback(self, callback: Callable, *args) -> None:
        """Run callback (handles both sync and async)"""
        try:
            result = callback(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            print(f"[AudioSink] Callback error: {e}")

    def cleanup(self) -> None:
        """Cleanup resources"""
        self.active_sessions.clear()
        self._known_speakers.clear()
        self._last_voice_time.clear()
        super().cleanup()


class SimpleAudioBuffer:
    """
    Simple buffer for collecting audio chunks and processing them

    Used when real-time streaming isn't possible.
    Collects audio for a period, then processes it.
    """

    def __init__(self, process_interval: float = 0.5):
        """
        Args:
            process_interval: How often to process buffered audio (seconds)
        """
        self.buffers: Dict[int, bytearray] = defaultdict(bytearray)
        self.process_interval = process_interval
        self._callbacks: Dict[int, Callable] = {}
        self._task: Optional[asyncio.Task] = None

    def add_audio(self, user_id: int, data: bytes) -> None:
        """Add audio data to user's buffer"""
        self.buffers[user_id].extend(data)

    def set_callback(self, user_id: int, callback: Callable[[bytes], Any]) -> None:
        """Set callback for when buffer is processed"""
        self._callbacks[user_id] = callback

    def remove_callback(self, user_id: int) -> None:
        """Remove callback for user"""
        self._callbacks.pop(user_id, None)
        self.buffers.pop(user_id, None)

    async def start_processing(self) -> None:
        """Start the buffer processing loop"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._process_loop())

    async def stop_processing(self) -> None:
        """Stop the buffer processing loop"""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _process_loop(self) -> None:
        """Main processing loop"""
        try:
            while True:
                await asyncio.sleep(self.process_interval)

                for user_id, buffer in list(self.buffers.items()):
                    if buffer and user_id in self._callbacks:
                        # Get data and clear buffer
                        data = bytes(buffer)
                        buffer.clear()

                        # Call callback
                        callback = self._callbacks[user_id]
                        try:
                            result = callback(data)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            print(f"[AudioBuffer] Process error: {e}")

        except asyncio.CancelledError:
            pass
