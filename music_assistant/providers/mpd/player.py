"""Music Player Daemon Player implementation."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from music_assistant_models.enums import PlaybackState, PlayerFeature, PlayerType

from music_assistant.models.player import Player, PlayerMedia

if TYPE_CHECKING:
    from mpd.asyncio import MPDClient

    from .provider import MPDPlayerProvider


class MPDPlayer(Player):
    """Music Player Daemon Player in Music Assistant."""

    def __init__(self, provider: MPDPlayerProvider, player_id: str, mpd: MPDClient) -> None:
        """Initialize the Music Player Daemon Player."""
        super().__init__(provider, player_id)
        self._mpd = mpd
        # init some static variables
        self._attr_name = f"MPD Player {player_id}"
        self._attr_type = PlayerType.PLAYER
        self._attr_supported_features = {
            PlayerFeature.VOLUME_SET,
            PlayerFeature.PAUSE,
            PlayerFeature.NEXT_PREVIOUS,
            PlayerFeature.SEEK,
            PlayerFeature.ENQUEUE,
        }
        self._attr_available = True
        self._needs_poll = False

    async def async_init(self) -> None:
        """Initialize MPD Server with async calls."""
        # Clear any previous MPD playback and force settings to expected values
        await asyncio.gather(
            self._mpd.clear(),  # Clear the current player queue
            self._mpd.repeat(0),  # Repeat Off
            self._mpd.single(0),  # Single Off
            self._mpd.random(0),  # Random Off
            self._mpd.consume(0),  # Consume Off
        )
        # Run the 'idle' function as an asyncio task to handle MPD Updates
        # async with TaskManager(self.provider.mass) as tg:
        #     tg.create_task(self.idle())
        self._set_attributes()
        self.update_state()

    @property
    def needs_poll(self) -> bool:
        """Return if the player needs to be polled for state updates."""
        return False

    @property
    def poll_interval(self) -> int:
        """Return the interval in seconds to poll the player for state updates."""
        return 5 if self.playback_state == PlaybackState.PLAYING else 30

    async def volume_set(self, volume_level: int) -> None:
        """Handle VOLUME_SET command on the player."""
        logger = self.provider.logger.getChild(self.player_id)
        logger.info(
            "Received VOLUME_SET command on player %s with level %s",
            self.display_name,
            volume_level,
        )
        await self._mpd.setvol(volume_level)
        self._attr_volume_level = volume_level
        self.update_state()

    async def play(self) -> None:
        """Play command."""
        logger = self.provider.logger.getChild(self.player_id)
        logger.info("Received PLAY command on player %s", self.display_name)
        await self._mpd.pause(0)
        self._attr_playback_state = PlaybackState.PLAYING
        self.update_state()

    async def stop(self) -> None:
        """Stop command."""
        logger = self.provider.logger.getChild(self.player_id)
        logger.info("Received STOP command on player %s", self.display_name)
        await self._mpd.stop()
        self._attr_playback_state = PlaybackState.IDLE
        self.update_state()

    async def pause(self) -> None:
        """Pause command."""
        logger = self.provider.logger.getChild(self.player_id)
        logger.info("Received PAUSE command on player %s", self.display_name)
        await self._mpd.pause(1)
        self._attr_playback_state = PlaybackState.PAUSED
        self.update_state()

    async def next_track(self) -> None:
        """Next command."""
        logger = self.provider.logger.getChild(self.player_id)
        logger.info("Received NEXT command on player %s", self.display_name)
        await self._mpd.next()
        self.update_state()

    async def previous_track(self) -> None:
        """Previous command."""
        logger = self.provider.logger.getChild(self.player_id)
        logger.info("Received NEXT command on player %s", self.display_name)
        await self._mpd.previous()
        self.update_state()

    async def seek(self, position: int) -> None:
        """SEEK command on the player."""
        logger = self.provider.logger.getChild(self.player_id)
        logger.info(
            "Received SEEK command on player %s with position %ds", self.display_name, position
        )
        await self._mpd.seekcur(position)
        self.update_state()

    async def play_media(self, media: PlayerMedia) -> None:
        """Play media command."""
        logger = self.provider.logger.getChild(self.player_id)
        logger.info(
            "Received PLAY_MEDIA command on player %s with uri %s", self.display_name, media.uri
        )
        await self._mpd.clear()
        await self._mpd.add(media.uri)
        await self._mpd.play()
        self._attr_current_media = media
        self._attr_playback_state = PlaybackState.PLAYING
        self.update_state()

    async def enqueue_next_media(self, media: PlayerMedia) -> None:
        """Handle enqueuing of the next (queue) item on the player."""
        logger = self.provider.logger.getChild(self.player_id)
        logger.info(
            "Received ENQUEUE command on player %s with uri %s", self.display_name, media.uri
        )
        await self._mpd.add(media.uri)
        self.update_state()

    async def poll(self) -> None:
        """Poll player for state updates."""
        self._set_attributes()
        self.update_state()

    async def on_unload(self) -> None:
        """Handle logic when the player is unloaded from the Player controller."""
        self.logger.info("Player %s unloaded", self.name)
        self._mpd.disconnect()

    def _set_attributes(self) -> None:
        """Update/set (dynamic) properties."""
        if self._mpd.connected:
            self._attr_available = True
            # status, song = await asyncio.gather(self._mpd.status(), self._mpd.currentsong())
            status = self._mpd.status()
            song = self._mpd.currentsong()
            # State
            if status["state"] == "play":
                self._attr_playback_state = PlaybackState.PLAYING
            elif status["state"] == "pause":
                self._attr_playback_state = PlaybackState.PAUSED
            elif status["state"] == "stop":
                self._attr_playback_state = PlaybackState.IDLE
            # Elapsed Time
            if "elapsed" in status:
                self._attr_elapsed_time = float(status["elapsed"])
            elif "time" in status:
                self._attr_elapsed_time = int(status["elapsed"].split(":")[0])
            else:
                self._attr_elapsed_time = 0
            self._attr_elapsed_time_last_updated = time.time()
            # Volume
            if "volume" in status:
                self._attr_volume_level = status["volume"]
            # Media
            if "file" in song:
                self._attr_current_itemm_id = song["file"]
            else:
                self._attr_current_itemm_id = ""
        else:
            self._attr_available = False

    async def idle(self) -> None:
        """Listen for MPD Subsystem Updates."""
        try:
            async for _ in self._mpd.idle(["player", "playlist", "mixer", "options"]):
                self._set_attributes()
        except Exception:
            self._attr_available = False
        self.update_state()
