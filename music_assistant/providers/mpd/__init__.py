"""Music Player Daemon Player Provider for Music Assistant."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from mpd.asyncio import MPDClient
from music_assistant_models.config_entries import ConfigEntry, ConfigValueType
from music_assistant_models.enums import ConfigEntryType, PlayerFeature, PlayerState, PlayerType
from music_assistant_models.errors import SetupFailedError
from music_assistant_models.player import DeviceInfo, Player, PlayerMedia

from music_assistant.constants import (
    CONF_ENTRY_HTTP_PROFILE,
    CONF_IP_ADDRESS,
    CONF_PASSWORD,
    CONF_PORT,
)
from music_assistant.models.player_provider import PlayerProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import (
        ConfigValueType,
        PlayerConfig,
        ProviderConfig,
    )
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    # setup is called when the user wants to setup a new provider instance.
    # you are free to do any preflight checks here and but you must return
    #  an instance of the provider.
    return MusicPlayerDaemonProvider(mass, manifest, config)


async def get_config_entries(
    mass: MusicAssistant,
    instance_id: str | None = None,
    action: str | None = None,
    values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    """
    Return Config entries to setup this provider.

    instance_id: id of an existing provider instance (None if new instance setup).
    action: [optional] action key called from config entries UI.
    values: the (intermediate) raw values for config entries sent with the action.
    """
    # ruff: noqa: ARG001
    return (
        ConfigEntry(
            key=CONF_IP_ADDRESS,
            type=ConfigEntryType.STRING,
            label="IP-Address (or hostname) of the device running mpd.",
            required=True,
        ),
        ConfigEntry(
            key=CONF_PASSWORD,
            type=ConfigEntryType.SECURE_STRING,
            label="Password to use to connect to mpd.",
            required=False,
        ),
        ConfigEntry(
            key=CONF_PORT,
            type=ConfigEntryType.INTEGER,
            default_value=6600,
            label="Port to use to connect to the mpd (default is 6600).",
            required=True,
            category="advanced",
        ),
    )


class MusicPlayerDaemonProvider(PlayerProvider):
    """Player Provider for Music Player Daemon (MPD) Players."""

    _mpd: MPDClient

    _mpd_state = {
        "play": PlayerState.PLAYING,
        "pause": PlayerState.PAUSED,
        "stop": PlayerState.IDLE,
    }

    @property
    def _player_id(self) -> str:
        return f"mpd_{self.config.get_value(CONF_IP_ADDRESS)}_{self.config.get_value(CONF_PORT)}"

    async def handle_async_init(self) -> None:
        """Handle async initialization of the provider."""
        self._mpd = MPDClient()

        if self.config.get_value(CONF_PASSWORD):
            self._mpd.password(self.config.get_value(CONF_PASSWORD))

        try:
            await self._mpd.connect(
                self.config.get_value(CONF_IP_ADDRESS),
                self.config.get_value(CONF_PORT),
            )
        except Exception as err:
            msg = f"Unable to start MPD connection ({err!s})"
            raise SetupFailedError(msg) from err

    async def loaded_in_mass(self) -> None:
        """Call after the provider has been loaded."""
        # Add MPD device to Player controller.
        player = self.mass.players.get(self._player_id, raise_unavailable=False)
        if not player:
            player = Player(
                player_id=self._player_id,
                provider=self.instance_id,
                type=PlayerType.PLAYER,
                name=f"MPD ({self.config.get_value(CONF_IP_ADDRESS)})",
                available=True,
                device_info=DeviceInfo(
                    ip_address=str(self.config.get_value(CONF_IP_ADDRESS)),
                ),
                supported_features={
                    PlayerFeature.ENQUEUE,
                    PlayerFeature.NEXT_PREVIOUS,
                    PlayerFeature.PAUSE,
                    PlayerFeature.SEEK,
                    PlayerFeature.VOLUME_SET,
                },
                needs_poll=True,
                poll_interval=10,
            )
        await self.mass.players.register_or_update(player)

        await asyncio.gather(
            self._mpd.clear(),  # Clear the current player queue
            self._mpd.repeat(0),  # Repeat Off
            self._mpd.single(0),  # Single Off
            self._mpd.random(0),  # Random Off
            self._mpd.consume(0),  # Consume Off
        )

        # Listen for MPD updates
        async for _ in self._mpd.idle(["player", "playlist", "mixer", "options"]):
            await self._handle_player_update()

    async def unload(self, is_removed: bool = False) -> None:
        """
        Handle unload/close of the provider.

        Called when provider is deregistered (e.g. MA exiting or config reloading).
        is_removed will be set to True when the provider is removed from the configuration.
        """
        self._mpd.disconnect()

    async def get_player_config_entries(self, player_id: str) -> tuple[ConfigEntry, ...]:
        """Return all (provider/player specific) Config Entries for the given player (if any)."""
        base_entries = await super().get_player_config_entries(player_id)
        return (
            *base_entries,
            CONF_ENTRY_HTTP_PROFILE,
        )

    async def on_player_config_change(self, config: PlayerConfig, changed_keys: set[str]) -> None:
        """Call (by config manager) when the configuration of a player changes."""
        # OPTIONAL
        # this will be called whenever a player config changes
        # you can use this to react to changes in player configuration
        # but this is completely optional and you can leave it out if not needed.

    async def cmd_stop(self, player_id: str) -> None:
        """Send STOP command to given player."""
        if not self.mass.players.get(player_id):
            return
        await self._mpd.stop()
        await self._handle_player_update()

    async def cmd_play(self, player_id: str) -> None:
        """Send PLAY (unpause) command to given player."""
        if not self.mass.players.get(player_id):
            return
        await self._mpd.pause(0)
        await self._handle_player_update()

    async def cmd_pause(self, player_id: str) -> None:
        """Send PAUSE command to given player."""
        if not self.mass.players.get(player_id):
            return
        await self._mpd.pause(1)
        await self._handle_player_update()

    async def cmd_next(self, player_id: str) -> None:
        """Handle NEXT TRACK command for given player."""
        if not self.mass.players.get(player_id):
            return
        await self._mpd.next()
        await self._handle_player_update()

    async def cmd_previous(self, player_id: str) -> None:
        """Handle PREVIOUS TRACK command for given player."""
        if not self.mass.players.get(player_id):
            return
        await self._mpd.previous()
        await self._handle_player_update()

    async def cmd_volume_set(self, player_id: str, volume_level: int) -> None:
        """Send VOLUME_SET command to given player."""
        if not self.mass.players.get(player_id):
            return
        await self._mpd.setvol(volume_level)
        await self._handle_player_update()

    async def cmd_seek(self, player_id: str, position: int) -> None:
        """Handle SEEK command for given queue.

        - player_id: player_id of the player to handle the command.
        - position: position in seconds to seek to in the current playing item.
        """
        if not self.mass.players.get(player_id):
            return
        await self._mpd.seekcur(position)
        await self._handle_player_update()

    async def play_media(
        self,
        player_id: str,
        media: PlayerMedia,
    ) -> None:
        """Handle PLAY MEDIA on given player.

        This is called by the Players controller to start playing a mediaitem on the given player.
        The provider's own implementation should work out how to handle this request.

            - player_id: player_id of the player to handle the command.
            - media: Details of the item that needs to be played on the player.
        """
        if not self.mass.players.get(player_id):
            return
        await self._mpd.clear()
        await self._mpd.add(media.uri)
        await self._mpd.play()
        await self._handle_player_update()

    async def enqueue_next_media(self, player_id: str, media: PlayerMedia) -> None:
        """
        Handle enqueuing of the next (queue) item on the player.

        Called when player reports it started buffering a queue item
        and when the queue items updated.

        A PlayerProvider implementation is in itself responsible for handling this
        so that the queue items keep playing until its empty or the player stopped.

        This will NOT be called if the end of the queue is reached (and repeat disabled).
        This will NOT be called if the player is using flow mode to playback the queue.
        """
        if not self.mass.players.get(player_id):
            return
        await self._mpd.add(media.uri)

    async def poll_player(self, player_id: str) -> None:
        """Poll player for state updates."""
        if not self.mass.players.get(player_id):
            return
        await self._handle_player_update()

    async def _handle_player_update(self) -> None:
        """Query Status from MPD."""
        if not (player := self.mass.players.get(self._player_id)):
            return

        status, song = await asyncio.gather(self._mpd.status(), self._mpd.currentsong())

        player.available = self._mpd.connected
        player.state = self._mpd_state[status["state"]]

        if "elapsed" in status:
            player.elapsed_time = float(status["elapsed"])
        elif "time" in status:
            player.elapsed_time = int(status["time"].split(":")[0])
        else:
            player.elapsed_time = 0

        player.elapsed_time_last_updated = time.time()

        if "volume" in status:
            player.volume_level = status["volume"]

        if "file" in song:
            player.current_item_id = song["file"]
        else:
            player.current_item_id = ""

        self.mass.players.update(self._player_id)
