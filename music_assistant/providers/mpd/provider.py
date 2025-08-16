"""Music Player Daemon Player Provider implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mpd.asyncio import MPDClient
from music_assistant_models.errors import SetupFailedError

from music_assistant.constants import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_PORT
from music_assistant.models.player_provider import PlayerProvider

from .player import MPDPlayer

if TYPE_CHECKING:
    from music_assistant_models.enums import ProviderFeature


class MPDPlayerProvider(PlayerProvider):
    """Music Player Daemon PlayerProvider."""

    @property
    def supported_features(self) -> set[ProviderFeature]:
        """Return the features supported by this Provider."""
        return set()

    async def handle_async_init(self) -> None:
        """Handle async initialization of the provider."""
        self.logger.info("Initializing MPDPlayerProvider with config: %s", self.config)
        mpd = MPDClient()
        if self.config.get_value(CONF_PASSWORD):
            mpd.password(self.config.get_value(CONF_PASSWORD))
        try:
            await mpd.connect(
                self.config.get_value(CONF_IP_ADDRESS),
                self.config.get_value(CONF_PORT),
            )
        except Exception as err:
            msg = f"Unable to start MPD connection ({err!s})"
            raise SetupFailedError(msg) from err
        player_id = (
            f"mpd://{self.config.get_value(CONF_IP_ADDRESS)}:{self.config.get_value(CONF_PORT)}"
        )
        player = MPDPlayer(self, player_id, mpd)
        await player.async_init()
        await self.mass.players.register(player)

    async def loaded_in_mass(self) -> None:
        """Call after the provider has been loaded."""
        # OPTIONAL
        # this is an optional method that you can implement if
        # relevant or leave out completely if not needed.
        # it will be called after the provider has been fully loaded into Music Assistant.
        # you can use this for instance to trigger custom (non-mdns) discovery of players
        # or any other logic that needs to run after the provider is fully loaded.
        self.logger.info("MPDPlayerProvider loaded")

    async def unload(self, is_removed: bool = False) -> None:
        """
        Handle unload/close of the provider.

        Called when provider is deregistered (e.g. MA exiting or config reloading).
        is_removed will be set to True when the provider is removed from the configuration.
        """
        for player in self.players:
            # if you have any cleanup logic for the players, you can do that here.
            # e.g. disconnecting from the player, closing connections, etc.
            self.logger.debug("Unloading player %s", player.name)
            await self.mass.players.unregister(player.player_id)
