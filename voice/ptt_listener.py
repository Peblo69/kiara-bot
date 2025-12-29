"""
Push-to-talk listener for local keyboard input.
"""

import asyncio
import logging

try:
    import keyboard as kb
except Exception:
    kb = None

logger = logging.getLogger("voice.ptt")


def _normalize_key(key: str) -> str:
    key = (key or "").strip().lower()
    if key in {"numpad3", "numpad_3", "num3", "num_3", "kp3"}:
        return "num 3"
    return key


class PushToTalkListener:
    def __init__(self, bot, voice_manager, key: str) -> None:
        self.bot = bot
        self.voice_manager = voice_manager
        self.key = _normalize_key(key)
        self._started = False

    def start(self) -> bool:
        if not kb:
            logger.warning("PTT disabled: keyboard package not available")
            return False
        if not self.key:
            logger.warning("PTT disabled: no key configured")
            return False
        if self._started:
            return True

        try:
            kb.on_press_key(self.key, self._on_press)
            kb.on_release_key(self.key, self._on_release)
        except Exception as e:
            logger.warning("PTT disabled: failed to register hotkey (%s)", e)
            return False

        self._started = True
        logger.info("PTT listening on key: %s", self.key)
        return True

    def _on_press(self, _event) -> None:
        self._schedule(self.voice_manager.handle_ptt_press())

    def _on_release(self, _event) -> None:
        self._schedule(self.voice_manager.handle_ptt_release())

    def _schedule(self, coro) -> None:
        loop = getattr(self.bot, "loop", None)
        if not loop or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(coro, loop)
