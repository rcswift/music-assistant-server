"""Demo Player Provider implementation."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from zeroconf import ServiceStateChange

from music_assistant.helpers.util import (
    get_port_from_zeroconf,
    get_primary_ip_address_from_zeroconf,
)
from music_assistant.models.player_provider import PlayerProvider

from .player import MusicPlayerDaemonPlayer

if TYPE_CHECKING:
    from zeroconf.asyncio import AsyncServiceInfo


class MusicPlayerDaemonPlayerProvider(PlayerProvider):
    """Music Player Daemon Player Provider."""

    async def handle_async_init(self) -> None:
        """Handle async initialization of the provider."""
        self.logger.info(
            "Initializing MusicPlayerDaemonPlayerProvider with config: %s", self.config
        )

    async def loaded_in_mass(self) -> None:
        """Call after the provider has been loaded."""
        self.logger.info("MusicPlayerDaemonPlayerProvider loaded")

    async def unload(self, is_removed: bool = False) -> None:
        """
        Handle unload/close of the provider.

        Called when provider is deregistered (e.g. MA exiting or config reloading).
        is_removed will be set to True when the provider is removed from the configuration.
        """
        for player in self.players:
            self.logger.debug("Unloading player %s", player.name)
            await self.mass.players.unregister(player.player_id)

    async def on_mdns_service_state_change(
        self, name: str, state_change: ServiceStateChange, info: AsyncServiceInfo | None
    ) -> None:
        """Handle MDNS service state callback."""
        if not info:
            return  # guard

        ip_address = get_primary_ip_address_from_zeroconf(info)
        port = get_port_from_zeroconf(info)

        assert ip_address is not None
        assert port is not None

        name_re = re.compile(r"(?:.*\@)?\s([a-zA-Z0-9_-]*)")
        match = name_re.search(name)
        if match:
            name = str(match.group(1))

        player_id = info.decoded_properties.get("uuid", name)

        if not player_id:
            return  # guard, we need a player_id to work with

        self.logger.debug(f"Handling MDNS Update for {player_id}")

        mass_player: MusicPlayerDaemonPlayer | None

        # handle removed player
        if state_change == ServiceStateChange.Removed:
            # check if the player manager has an existing entry for this player
            if mass_player := cast(
                "MusicPlayerDaemonPlayer | None", self.mass.players.get(player_id)
            ):
                # the player has become unavailable
                self.logger.info("Player offline: %s", mass_player.display_name)
                await self.mass.players.unregister(player_id)
            return

        # handle update for existing device
        if mass_player := cast("MusicPlayerDaemonPlayer | None", self.mass.players.get(player_id)):
            # existing player found in the player manager,
            # this is an existing player that has been updated/reconnected
            # or simply a re-announcement on mdns.
            if ip_address and ip_address == mass_player.device_info.ip_address:
                if not mass_player.available:
                    self.logger.info(f"Player {mass_player.display_name} reconnected")
                    await mass_player.setup()
                    return

        # handle new player
        self.logger.info("Discovered device %s on %s", name, ip_address)

        mpd_player = MusicPlayerDaemonPlayer(self, player_id, ip_address, port)
        await mpd_player.setup()
        await self.mass.players.register_or_update(mpd_player)
