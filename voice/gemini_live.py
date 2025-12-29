"""
Gemini Live API Integration
Real-time voice-to-voice conversation with native audio
"""

import asyncio
import os
from typing import Callable, Optional, Any
from google import genai
from google.genai import types


class GeminiLiveSession:
    """
    Real-time voice conversation session with Gemini Live API

    This uses native audio processing - no separate STT/TTS needed!
    Latency: ~300-400ms response time
    """

    # Model for native audio (voice-to-voice)
    MODEL = "gemini-2.5-flash-preview-native-audio-dialog"

    # Alternative models:
    # "gemini-2.0-flash-live-001" - Half-cascade (good quality)
    # "gemini-2.5-flash-native-audio-preview" - Native audio preview

    SYSTEM_PROMPT = """You are Kiara, a friendly, witty, and helpful AI assistant living in a Discord voice channel.

Your personality:
- Warm and approachable, like talking to a smart friend
- Quick with responses, conversational tone
- You can be playful but always helpful
- You speak naturally, not robotically

Important rules:
- Keep responses SHORT and conversational (1-3 sentences usually)
- Don't say "As an AI" or mention being an assistant
- React naturally to what users say
- If someone says "stop", "end", "bye", "thanks Kiara" - say a quick goodbye

You're hanging out in a Discord server helping people with whatever they need - coding, questions, jokes, advice, or just chatting."""

    # Available voices (30 HD voices)
    VOICES = [
        "Puck", "Charon", "Kore", "Fenrir", "Aoede",
        "Leda", "Orus", "Zephyr", "Nova", "Stella"
    ]

    def __init__(
        self,
        user_id: int,
        on_audio_response: Optional[Callable[[bytes], Any]] = None,
        on_text_response: Optional[Callable[[str], Any]] = None,
        on_turn_complete: Optional[Callable[[], Any]] = None,
        voice: str = "Kore"
    ):
        """
        Initialize a Gemini Live session

        Args:
            user_id: Discord user ID for this session
            on_audio_response: Callback when audio bytes are received
            on_text_response: Callback when text transcript is received
            on_turn_complete: Callback when AI finishes speaking
            voice: Voice to use (default: Kore - female, friendly)
        """
        self.user_id = user_id
        self.on_audio_response = on_audio_response
        self.on_text_response = on_text_response
        self.on_turn_complete = on_turn_complete
        self.voice = voice if voice in self.VOICES else "Kore"

        self.client = None
        self.session = None
        self.is_active = False
        self._receive_task = None
        self._audio_queue = asyncio.Queue()

    async def connect(self) -> bool:
        """
        Establish connection to Gemini Live API

        Returns:
            True if connected successfully
        """
        try:
            # Initialize client with API key from environment
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY not set in environment")

            self.client = genai.Client(api_key=api_key)

            # Configuration for native audio
            config = {
                "response_modalities": ["AUDIO", "TEXT"],
                "system_instruction": self.SYSTEM_PROMPT,
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {
                            "voice_name": self.voice
                        }
                    }
                }
            }

            # Connect to Live API
            self.session = await self.client.aio.live.connect(
                model=self.MODEL,
                config=config
            )

            self.is_active = True

            # Start receiving responses in background
            self._receive_task = asyncio.create_task(self._receive_loop())

            return True

        except Exception as e:
            print(f"[GeminiLive] Connection error: {e}")
            self.is_active = False
            return False

    async def send_audio(self, audio_chunk: bytes) -> None:
        """
        Send audio data to Gemini

        Args:
            audio_chunk: PCM audio bytes (16kHz, mono, 16-bit)
        """
        if not self.is_active or not self.session:
            return

        try:
            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=audio_chunk,
                    mime_type="audio/pcm;rate=16000"
                )
            )
        except Exception as e:
            print(f"[GeminiLive] Send error: {e}")

    async def send_text(self, text: str) -> None:
        """
        Send text input to Gemini (for hybrid mode)

        Args:
            text: Text message to send
        """
        if not self.is_active or not self.session:
            return

        try:
            await self.session.send_realtime_input(text=text)
        except Exception as e:
            print(f"[GeminiLive] Text send error: {e}")

    async def _receive_loop(self) -> None:
        """Background task to receive and process Gemini responses"""
        try:
            while self.is_active and self.session:
                turn = self.session.receive()

                async for response in turn:
                    if not self.is_active:
                        break

                    # Handle server content (AI responses)
                    if response.server_content:
                        model_turn = response.server_content.model_turn

                        if model_turn and model_turn.parts:
                            for part in model_turn.parts:
                                # Audio response
                                if hasattr(part, 'inline_data') and part.inline_data:
                                    if part.inline_data.mime_type and 'audio' in part.inline_data.mime_type:
                                        audio_bytes = part.inline_data.data
                                        if self.on_audio_response and audio_bytes:
                                            await self._safe_callback(
                                                self.on_audio_response,
                                                audio_bytes
                                            )

                                # Text response (transcript)
                                if hasattr(part, 'text') and part.text:
                                    if self.on_text_response:
                                        await self._safe_callback(
                                            self.on_text_response,
                                            part.text
                                        )

                        # Turn complete signal
                        if response.server_content.turn_complete:
                            if self.on_turn_complete:
                                await self._safe_callback(self.on_turn_complete)

                    # Handle interruption (user started speaking while AI was talking)
                    if response.server_content and response.server_content.interrupted:
                        # Clear any queued audio - user interrupted
                        while not self._audio_queue.empty():
                            try:
                                self._audio_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[GeminiLive] Receive error: {e}")
            self.is_active = False

    async def _safe_callback(self, callback: Callable, *args) -> None:
        """Safely execute callback (sync or async)"""
        try:
            result = callback(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            print(f"[GeminiLive] Callback error: {e}")

    async def disconnect(self) -> None:
        """Close the session and cleanup"""
        self.is_active = False

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None

        self.client = None

    @property
    def connected(self) -> bool:
        """Check if session is connected and active"""
        return self.is_active and self.session is not None
