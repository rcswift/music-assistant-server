"""Demo Player Provider implementation."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

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
    """
    Example/demo Player provider.

    Note that this is always subclassed from PlayerProvider,
    which in turn is a subclass of the generic Provider model.

    The base implementation already takes care of some convenience methods,
    such as the mass object and the logger. Take a look at the base class
    for more information on what is available.

    Just like with any other subclass, make sure that if you override
    any of the default methods (such as __init__), you call the super() method.
    In most cases its not needed to override any of the builtin methods and you only
    implement the abc methods with your actual implementation.
    """

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
        # OPTIONAL
        # this is an optional method that you can implement if
        # relevant or leave out completely if not needed.
        # it will be called when the provider is unloaded from Music Assistant.
        # this means also when the provider is getting reloaded
        for player in self.players:
            # if you have any cleanup logic for the players, you can do that here.
            # e.g. disconnecting from the player, closing connections, etc.
            self.logger.debug("Unloading player %s", player.name)
            await self.mass.players.unregister(player.player_id)

    def on_player_enabled(self, player_id: str) -> None:
        """Call (by config manager) when a player gets enabled."""
        # OPTIONAL
        # this is an optional method that you can implement if
        # you want to do something special when a player is enabled.

    def on_player_disabled(self, player_id: str) -> None:
        """Call (by config manager) when a player gets disabled."""
        # OPTIONAL
        # this is an optional method that you can implement if
        # you want to do something special when a player is disabled.
        # e.g. you can stop polling the player or disconnect from it.

    async def remove_player(self, player_id: str) -> None:
        """Remove a player from this provider."""
        # OPTIONAL - required only if you specified ProviderFeature.REMOVE_PLAYER
        # this is used to actually remove a player.

    async def on_mdns_service_state_change(
        self, name: str, state_change: ServiceStateChange, info: AsyncServiceInfo | None
    ) -> None:
        """Handle MDNS service state callback."""
        # MANDATORY IF YOU WANT TO USE MDNS DISCOVERY
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

        # handle removed player
        if state_change == ServiceStateChange.Removed:
            # check if the player manager has an existing entry for this player
            if mass_player := self.mass.players.get(player_id):
                # the player has become unavailable
                self.logger.debug("Player offline: %s", mass_player.display_name)
                await self.mass.players.unregister(player_id)
            return

        # handle update for existing device
        # (state change is either updated or added)
        # check if we have an existing player in the player manager
        # note that you can use this point to update the player connection info
        # if that changed (e.g. ip address)
        if mass_player := self.mass.players.get(player_id):
            # existing player found in the player manager,
            # this is an existing player that has been updated/reconnected
            # or simply a re-announcement on mdns.
            if ip_address and ip_address != mass_player.device_info.ip_address:
                self.logger.debug(
                    "Address updated to %s for player %s", ip_address, mass_player.display_name
                )
            # inform the player manager of any changes to the player object
            # note that you would normally call this from some other callback from
            # the player's native api/library which informs you of changes in the player state.
            # as a last resort you can also choose to let the player manager
            # poll the player for state changes
            mass_player.update_state()
            return

        # handle new player
        self.logger.debug("Discovered device %s on %s", name, ip_address)

        mpd_player = MusicPlayerDaemonPlayer(self, player_id, ip_address, port)
        await mpd_player.setup()
        await self.mass.players.register_or_update(mpd_player)
