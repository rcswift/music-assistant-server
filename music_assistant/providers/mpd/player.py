"""Music Player Daemon Player implementation."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from mpd.asyncio import MPDClient
from music_assistant_models.enums import PlaybackState, PlayerFeature, PlayerType

from music_assistant.models.player import DeviceInfo, Player, PlayerMedia

if TYPE_CHECKING:
    from .provider import MusicPlayerDaemonPlayerProvider

PLAYBACK_STATE_MAP = {
    "play": PlaybackState.PLAYING,
    "stop": PlaybackState.IDLE,
    "pause": PlaybackState.PAUSED,
}


class MusicPlayerDaemonPlayer(Player):
    """Music Player Daemon Player in Music Assistant."""

    _mpd: MPDClient

    def __init__(
        self,
        provider: MusicPlayerDaemonPlayerProvider,
        player_id: str,
        ip_address: str,
        port: int,
    ) -> None:
        """Initialize the Music Player Daemon Player."""
        super().__init__(provider, player_id)
        self.ip_address = ip_address
        self.port = port
        # init some static variables
        self._attr_name = f"MPD {player_id}"
        self._attr_type = PlayerType.PLAYER
        self._attr_supported_features = {
            PlayerFeature.VOLUME_SET,
            PlayerFeature.PAUSE,
            PlayerFeature.NEXT_PREVIOUS,
            PlayerFeature.SEEK,
            PlayerFeature.ENQUEUE,
        }
        self._attr_device_info = DeviceInfo(
            ip_address=ip_address,
        )
        self._needs_poll = False
        self._mpd = MPDClient()

    @property
    def needs_poll(self) -> bool:
        """Return if the player needs to be polled for state updates."""
        # MANDATORY
        # this should return True if the player needs to be polled for state updates,
        # If you player does not need to be polled, you can return False.
        return False

    @property
    def poll_interval(self) -> int:
        """Return the interval in seconds to poll the player for state updates."""
        # OPTIONAL
        # used in conjunction with the needs_poll property.
        # this should return the interval in seconds to poll the player for state updates.
        return 5 if self.playback_state == PlaybackState.PLAYING else 30

    async def setup(self) -> None:
        """Set up the player."""
        # Establish the player connection
        await self._mpd.connect(self.ip_address, self.port)
        # Clear any previous MPD playback and force settings to expected values
        await asyncio.gather(
            self._mpd.clear(),  # Clear the current player queue
            self._mpd.repeat(0),  # Repeat Off
            self._mpd.single(0),  # Single Off
            self._mpd.random(0),  # Random Off
            self._mpd.consume(0),  # Consume Off
        )
        # Run the 'idle' function as an asyncio task to handle MPD Updates
        self.mass.create_task(self.idle())
        self._attr_available = True
        self.update_state()
        logger = self.provider.logger.getChild(self.player_id)
        logger.debug("Setup Complete")

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
        await self.get_mpd_status()

    async def on_unload(self) -> None:
        """Handle logic when the player is unloaded from the Player controller."""
        logger = self.provider.logger.getChild(self.player_id)
        self._mpd.disconnect()
        logger.info("Player %s unloaded", self.name)

    async def get_mpd_status(self) -> None:
        """Update/set (dynamic) properties."""
        logger = self.provider.logger.getChild(self.player_id)
        if self._mpd.connected:
            self._attr_available = True
            status, song = await asyncio.gather(self._mpd.status(), self._mpd.currentsong())

            # State
            if "state" in status:
                self._attr_playback_state = PLAYBACK_STATE_MAP[status["state"]]
            logger.debug(f"PlaybackState : {self._attr_playback_state}")

            # Volume
            if "volume" in status:
                self._attr_volume_level = status["volume"]
            logger.debug(f"VolumeLevel : {self._attr_volume_level}")

            # Elapsed Time
            if "elapsed" in status:
                self._attr_elapsed_time = float(status["elapsed"])
            elif "time" in status:
                self._attr_elapsed_time = int(status["elapsed"].split(":")[0])
            else:
                self._attr_elapsed_time = 0
            self._attr_elapsed_time_last_updated = time.time()
            logger.debug(f"ElapsedTime : {self._attr_elapsed_time}")
        else:
            self._attr_available = False
        self.update_state()

    async def idle(self) -> None:
        """Listen for MPD Subsystem Updates."""
        logger = self.provider.logger.getChild(self.player_id)
        try:
            async for subsystem in self._mpd.idle(["player", "playlist", "mixer", "options"]):
                logger.debug(f"Idle Response: {subsystem}")
                await self.get_mpd_status()
        except Exception:
            self._attr_available = False
        self.update_state()
