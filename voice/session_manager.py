"""
Voice Session Manager
Orchestrates voice conversations - ties everything together
"""

import asyncio
import logging
from typing import Dict, Optional, Any, Set
from dataclasses import dataclass, field
from datetime import datetime
import discord

logger = logging.getLogger("voice.session")

from .audio_utils import AudioProcessor
from .gemini_live import GeminiLiveSession
from .audio_sink import KiaraAudioSink, SimpleAudioBuffer
from .audio_player import VoicePlayer


@dataclass
class UserVoiceSession:
    """Represents an active voice conversation with a user"""
    user_id: int
    user: discord.Member
    gemini_session: GeminiLiveSession
    started_at: datetime = field(default_factory=datetime.utcnow)
    message_count: int = 0
    is_speaking: bool = False


class VoiceSessionManager:
    """
    Manages voice AI sessions in Discord

    Flow:
    1. Bot joins voice channel
    2. Listens for wake word ("Hey Kiara")
    3. Starts Gemini session for that user
    4. Streams audio bidirectionally
    5. Ends on "stop"/"bye" or timeout
    """

    # End phrases that close the session
    END_PHRASES = [
        "stop", "end", "bye", "goodbye", "see you",
        "thanks kiara", "thank you kiara", "that's all",
        "nevermind", "never mind", "cancel"
    ]

    # Timeout for inactive sessions (seconds)
    SESSION_TIMEOUT = 60

    def __init__(self, bot: discord.Bot):
        """
        Initialize the session manager

        Args:
            bot: Discord bot instance
        """
        self.bot = bot

        # Active connections per guild
        self._voice_clients: Dict[int, discord.VoiceClient] = {}

        # Audio components per guild
        self._audio_sinks: Dict[int, KiaraAudioSink] = {}
        self._audio_players: Dict[int, VoicePlayer] = {}

        # Active user sessions
        self._sessions: Dict[int, UserVoiceSession] = {}

        # Users waiting in queue (when someone else is talking)
        self._queue: Dict[int, list] = {}  # guild_id -> [user_ids]

        # Lock for thread safety
        self._lock = asyncio.Lock()

        # Push-to-talk (PTT) controls
        self._ptt_enabled = False
        self._ptt_owner: Optional[tuple[int, int]] = None  # (guild_id, user_id)
        self._ptt_key_down = False

    async def join_channel(self, channel: discord.VoiceChannel) -> bool:
        """
        Join a voice channel and start listening

        Args:
            channel: Discord voice channel to join

        Returns:
            True if successfully joined
        """
        guild_id = channel.guild.id
        logger.info(f"[VoiceManager] join_channel called for {channel.name} (guild {guild_id})")

        try:
            # Check if already connected to this guild
            if guild_id in self._voice_clients:
                vc = self._voice_clients[guild_id]
                if vc.is_connected():
                    logger.info(f"[VoiceManager] Already connected to voice in guild {guild_id}")
                    return True
                else:
                    # Clean up stale connection
                    logger.info("[VoiceManager] Cleaning up stale connection...")
                    await self.leave_channel(guild_id)

            # Small delay to ensure gateway is stable
            await asyncio.sleep(0.5)

            # Check if bot is already in a voice channel (not tracked by us)
            guild = channel.guild
            if guild.voice_client:
                # Disconnect existing connection
                logger.info("[VoiceManager] Disconnecting existing voice client...")
                try:
                    await guild.voice_client.disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(2.0)  # Wait longer for cleanup

            # Connect to voice channel with retry logic
            logger.info(f"[VoiceManager] Calling channel.connect(timeout=60)...")

            # Try connecting with retries
            vc = None
            for attempt in range(3):
                try:
                    logger.info(f"[VoiceManager] Connection attempt {attempt + 1}/3")
                    vc = await channel.connect(timeout=60.0, reconnect=False)
                    if vc and vc.is_connected():
                        break
                except Exception as conn_err:
                    logger.warning(f"[VoiceManager] Attempt {attempt + 1} failed: {conn_err}")
                    # Force cleanup between attempts to avoid "already connected" state
                    try:
                        await self.leave_channel(guild_id)
                    except Exception:
                        pass
                    if attempt < 2:
                        await asyncio.sleep(2.0)  # Wait before retry
                    else:
                        raise

            if not vc:
                logger.error("[VoiceManager] All connection attempts failed")
                return False

            logger.info(f"[VoiceManager] channel.connect() returned! vc={vc}")
            self._voice_clients[guild_id] = vc

            # Wait for connection to stabilize
            await asyncio.sleep(1.0)

            # Verify connection
            if not vc.is_connected():
                logger.error(f"[VoiceManager] Connection not established after connect()")
                return False

            logger.info("[VoiceManager] Connection established, setting up audio...")

            # Create audio player
            self._audio_players[guild_id] = VoicePlayer(vc)

            # Create audio sink for receiving
            sink = KiaraAudioSink(
                on_audio_chunk=lambda uid, data: self._on_audio_chunk(guild_id, uid, data),
                on_user_speaking=lambda uid, member: self._on_user_speaking(guild_id, uid, member)
            )
            self._audio_sinks[guild_id] = sink

            # Skip recording for now - just stay in voice
            # TODO: Re-enable once voice connection is stable
            logger.info("[VoiceManager] Skipping audio recording for stability test")

            self._queue[guild_id] = []

            logger.info(f"[VoiceManager] Successfully joined {channel.name} in {channel.guild.name}")
            return True

        except asyncio.TimeoutError as e:
            logger.error(f"[VoiceManager] TIMEOUT joining channel: {e}")
            return False
        except Exception as e:
            logger.error(f"[VoiceManager] Failed to join channel: {e}", exc_info=True)
            return False

    def _on_recording_stopped(self, sink: KiaraAudioSink, guild_id: int) -> None:
        """Callback when recording stops"""
        print(f"[VoiceManager] Recording stopped in guild {guild_id}")

    def _on_user_speaking(self, guild_id: int, user_id: int, member: Optional[discord.Member]) -> None:
        """Called when a new user starts speaking - auto-start session for cloud mode"""
        logger.info(f"[VoiceManager] User {user_id} started speaking in guild {guild_id}")

        # Auto-start session if not in PTT mode and user doesn't have one
        if not self._ptt_enabled and user_id not in self._sessions and member:
            logger.info(f"[VoiceManager] Auto-starting session for {member.display_name}")
            asyncio.create_task(self.start_session(guild_id, user_id, member))

    async def leave_channel(self, guild_id: int) -> None:
        """
        Leave voice channel and cleanup

        Args:
            guild_id: Guild ID to leave
        """
        logger.info(f"[VoiceManager] leave_channel called for guild {guild_id}")
        # End all sessions in this guild
        sessions_to_end = [
            uid for uid, session in self._sessions.items()
            if session.user.guild.id == guild_id
        ]
        for uid in sessions_to_end:
            await self.end_session(uid)

        # Cleanup audio sink
        if guild_id in self._audio_sinks:
            self._audio_sinks[guild_id].cleanup()
            del self._audio_sinks[guild_id]

        # Cleanup audio player
        if guild_id in self._audio_players:
            await self._audio_players[guild_id].cleanup()
            del self._audio_players[guild_id]

        # Disconnect voice client
        if guild_id in self._voice_clients:
            vc = self._voice_clients[guild_id]
            if vc.is_connected():
                await vc.disconnect()
            del self._voice_clients[guild_id]

        # Clear queue
        self._queue.pop(guild_id, None)

        print(f"[VoiceManager] Left voice channel in guild {guild_id}")

    async def start_session(self, guild_id: int, user_id: int, user: discord.Member) -> bool:
        """
        Start a voice conversation session with a user

        Args:
            guild_id: Guild ID
            user_id: User's Discord ID
            user: Discord Member object

        Returns:
            True if session started successfully
        """
        async with self._lock:
            logger.info(f"[VoiceManager] start_session: guild={guild_id} user={user_id}")
            # Check if user already has session
            if user_id in self._sessions:
                return True

            # Check if another user is active in this guild
            guild_sessions = [
                s for s in self._sessions.values()
                if s.user.guild.id == guild_id
            ]
            if guild_sessions:
                # Add to queue
                if user_id not in self._queue.get(guild_id, []):
                    self._queue.setdefault(guild_id, []).append(user_id)
                    await self._notify_queued(guild_id, user)
                return False

            # Create Gemini session
            gemini = GeminiLiveSession(
                user_id=user_id,
                on_audio_response=lambda audio: self._on_gemini_audio(guild_id, user_id, audio),
                on_text_response=lambda text: self._on_gemini_text(guild_id, user_id, text),
                on_turn_complete=lambda: self._on_turn_complete(guild_id, user_id)
            )

            # Connect to Gemini
            connected = await gemini.connect()
            if not connected:
                logger.error(f"[VoiceManager] Failed to connect Gemini for user {user_id}")
                return False

            # Create session
            session = UserVoiceSession(
                user_id=user_id,
                user=user,
                gemini_session=gemini
            )
            if self._ptt_enabled and self._ptt_owner and self._ptt_owner[1] == user_id:
                session.is_speaking = self._ptt_key_down
            self._sessions[user_id] = session

            # Mark user as active in audio sink
            if guild_id in self._audio_sinks:
                self._audio_sinks[guild_id].start_session(user_id)

            print(f"[VoiceManager] Started session for {user.display_name}")

            # Send greeting
            await gemini.send_text("The user just activated you by saying 'Hey Kiara'. Greet them briefly and ask how you can help.")

            return True

    async def end_session(self, user_id: int) -> None:
        """
        End a user's voice session

        Args:
            user_id: User's Discord ID
        """
        async with self._lock:
            if user_id not in self._sessions:
                return

            session = self._sessions[user_id]
            guild_id = session.user.guild.id
            logger.info(f"[VoiceManager] end_session: guild={guild_id} user={user_id}")

            # Disconnect Gemini
            await session.gemini_session.disconnect()

            # Remove from audio sink active sessions
            if guild_id in self._audio_sinks:
                self._audio_sinks[guild_id].end_session(user_id)

            # Remove session
            del self._sessions[user_id]

            print(f"[VoiceManager] Ended session for {session.user.display_name}")

            # Start next user in queue
            await self._process_queue(guild_id)

    async def _process_queue(self, guild_id: int) -> None:
        """Process the next user in queue"""
        queue = self._queue.get(guild_id, [])
        if not queue:
            return

        next_user_id = queue.pop(0)

        # Find the user
        vc = self._voice_clients.get(guild_id)
        if not vc:
            return

        for member in vc.channel.members:
            if member.id == next_user_id:
                await self.start_session(guild_id, next_user_id, member)
                break

    async def _notify_queued(self, guild_id: int, user: discord.Member) -> None:
        """Notify user they're in queue"""
        # TODO: Play a short audio message saying they're in queue
        print(f"[VoiceManager] {user.display_name} added to queue")

    def _on_wake_word(self, guild_id: int, user_id: int, user: discord.Member) -> None:
        """Called when wake word detected"""
        asyncio.create_task(self.start_session(guild_id, user_id, user))

    def _on_audio_chunk(self, guild_id: int, user_id: int, audio_data: bytes) -> None:
        """Called when audio received from user"""
        logger.debug(f"[VoiceManager] Audio chunk: guild={guild_id} user={user_id} bytes={len(audio_data)}")
        if user_id not in self._sessions:
            return

        session = self._sessions[user_id]
        if self._ptt_enabled and not session.is_speaking:
            return

        # Convert Discord audio to Gemini format
        gemini_audio = AudioProcessor.discord_to_gemini(audio_data)

        # Send to Gemini
        asyncio.create_task(session.gemini_session.send_audio(gemini_audio))

    def _on_silence(self, guild_id: int, user_id: int) -> None:
        """Called when user stops speaking"""
        if user_id in self._sessions:
            self._sessions[user_id].is_speaking = False

    def _on_gemini_audio(self, guild_id: int, user_id: int, audio_bytes: bytes) -> None:
        """Called when Gemini sends audio response"""
        logger.debug(f"[VoiceManager] Gemini audio: guild={guild_id} user={user_id} bytes={len(audio_bytes)}")
        if guild_id not in self._audio_players:
            return

        player = self._audio_players[guild_id]
        asyncio.create_task(player.queue_audio(audio_bytes))

    def _on_gemini_text(self, guild_id: int, user_id: int, text: str) -> None:
        """Called when Gemini sends text (transcript)"""
        logger.info(f"[Kiara] {text}")

        # Check for end phrases
        text_lower = text.lower()
        if any(phrase in text_lower for phrase in self.END_PHRASES):
            # Gemini said goodbye, end session after a delay
            asyncio.create_task(self._delayed_end_session(user_id, 2.0))

    def _on_turn_complete(self, guild_id: int, user_id: int) -> None:
        """Called when Gemini finishes speaking"""
        pass  # Could use this for turn-taking logic

    async def _delayed_end_session(self, user_id: int, delay: float) -> None:
        """End session after delay (allows goodbye to play)"""
        await asyncio.sleep(delay)
        await self.end_session(user_id)

    def get_active_session(self, guild_id: int) -> Optional[UserVoiceSession]:
        """Get active session for a guild"""
        for session in self._sessions.values():
            if session.user.guild.id == guild_id:
                return session
        return None

    def is_connected(self, guild_id: int) -> bool:
        """Check if bot is connected to voice in guild"""
        vc = self._voice_clients.get(guild_id)
        return vc is not None and vc.is_connected()

    def enable_ptt(self, enabled: bool) -> None:
        """Enable or disable push-to-talk gating"""
        self._ptt_enabled = enabled
        logger.info(f"[VoiceManager] PTT enabled: {enabled}")

    def is_ptt_enabled(self) -> bool:
        """Return True if push-to-talk is enabled"""
        return self._ptt_enabled

    def get_ptt_owner(self) -> Optional[tuple[int, int]]:
        """Get the current PTT owner (guild_id, user_id)"""
        return self._ptt_owner

    def set_ptt_owner(self, guild_id: int, user_id: int) -> None:
        """Set the Discord user that local PTT controls"""
        self._ptt_owner = (guild_id, user_id)
        logger.info(f"[VoiceManager] PTT owner set to user {user_id} in guild {guild_id}")

    async def handle_ptt_press(self) -> None:
        """Handle local push-to-talk press event"""
        if not self._ptt_enabled:
            return
        if self._ptt_key_down:
            return
        self._ptt_key_down = True
        logger.info("[VoiceManager] PTT press")

        if not self._ptt_owner:
            logger.warning("[VoiceManager] PTT press ignored: no owner set")
            return

        guild_id, user_id = self._ptt_owner
        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning("[VoiceManager] PTT press ignored: guild not found")
            return

        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None

        if not member or not member.voice or not member.voice.channel:
            logger.warning("[VoiceManager] PTT press ignored: user not in voice")
            return

        if not self.is_connected(guild_id):
            joined = await self.join_channel(member.voice.channel)
            if not joined:
                logger.warning("[VoiceManager] PTT press failed: could not join channel")
                return

        started = await self.start_session(guild_id, user_id, member)
        if not started:
            return

        session = self._sessions.get(user_id)
        if session:
            session.is_speaking = True

    async def handle_ptt_release(self) -> None:
        """Handle local push-to-talk release event"""
        if not self._ptt_enabled:
            return
        if not self._ptt_key_down:
            return
        self._ptt_key_down = False
        logger.info("[VoiceManager] PTT release")

        if not self._ptt_owner:
            return

        _, user_id = self._ptt_owner
        session = self._sessions.get(user_id)
        if session:
            session.is_speaking = False

    async def trigger_wake(self, guild_id: int, user: discord.Member) -> bool:
        """
        Manually trigger wake word (for testing via command)

        Args:
            guild_id: Guild ID
            user: User who triggered

        Returns:
            True if session started
        """
        return await self.start_session(guild_id, user.id, user)


# Global instance (will be initialized by bot)
voice_manager: Optional[VoiceSessionManager] = None


def get_voice_manager() -> Optional[VoiceSessionManager]:
    """Get the global voice manager instance"""
    return voice_manager


def init_voice_manager(bot: discord.Bot) -> VoiceSessionManager:
    """Initialize the global voice manager"""
    global voice_manager
    voice_manager = VoiceSessionManager(bot)
    return voice_manager
