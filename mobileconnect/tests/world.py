# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gord Tulloch

"""Core's real Connect Link listener, peers, pairing, lease, registry and Gateway around fake devices, so the routes
are tested over the real wire. Also run by Galileo-MobileConnect's tests, as a Master for the real Swift client."""

from __future__ import annotations

from galileo.bus import EventBus
from galileo.connect_link.audit import AuditLog
from galileo.connect_link.certs import CertificateStore
from galileo.connect_link.gateway import Gateway
from galileo.connect_link.handshake import Handshake, Versions
from galileo.connect_link.pairing import PairingService
from galileo.connect_link.peers import PeerStore
from galileo.connect_link.secrets_store import MemorySecretStore
from galileo.connect_link.server import PROTOCOL_VERSION, LinkServer
from galileo.control_surface import LeaseBroker, ObservableState
from galileo.control_surface.catalogue import register_device_operations
from galileo.control_surface.feed import EventFeed
from galileo.control_surface.holds import HoldManager
from galileo.control_surface.registry import Registry
from galileo.control_surface.service import ControlSurfaceService

PIER = "1"


class FakeMount:
    is_connected = True

    def __init__(self) -> None:
        self.tracking, self.parked, self.calls = True, False, []

    async def get_status(self):
        return {"right_ascension": 5.5, "declination": -5.0, "altitude": 40.0, "azimuth": 170.0, "tracking": self.tracking,
                "slewing": False, "at_park": self.parked, "side_of_pier": "West", "tracking_rate": "Sidereal"}

    async def set_tracking(self, enabled):
        self.calls.append(("track", enabled))
        self.tracking = enabled

    async def park(self):
        self.calls.append(("park",))
        self.parked = True

    async def unpark(self):
        self.parked = False

    async def abort_slew(self):
        self.calls.append(("abort",))

    async def slew_to_coordinates(self, ra, dec):
        self.calls.append(("slew", ra, dec))


class FakeCamera:
    is_connected = True

    def get_temperature(self):
        return -10.0

    def get_cooler_power(self):
        return 42.0

    async def abort_exposure(self):
        pass


class FakeFocus:
    def __init__(self) -> None:
        self.at = 21000
        self.holds = 0

    def position(self):
        return self.at

    def temperature(self):
        return 4.5

    async def move_by(self, steps):
        self.at += steps

    async def move_to(self, position):
        self.at = position

    async def halt(self):
        pass

    def start_hold(self, holds, direction, principal, origin=""):
        self.holds += 1
        return holds.start(PIER, principal, lambda: None, origin=origin)


class FakeDevices:
    def __init__(self) -> None:
        self.mount, self.camera, self.focus = FakeMount(), FakeCamera(), FakeFocus()

    def controller(self, pier, category):
        return {"mount": self.mount, "camera": self.camera}.get(category) if pier == PIER else None

    def manual_focus(self, pier):
        return self.focus if pier == PIER else None

    def stop_session(self, pier):
        return None


class World:
    """Everything a Master has, in one object, with the listener stopped until ``start``."""

    def __init__(self) -> None:
        from mobileconnect.clients import ClientRegistry
        from mobileconnect.plugin import Runtime

        self.bus = EventBus()
        self.devices = FakeDevices()
        self.holds = HoldManager()
        self.registry = Registry()
        register_device_operations(self.registry, self.devices, self.holds)
        self.leases = LeaseBroker(bus=self.bus)
        self.audit = AuditLog()
        self.gateway = Gateway(self.registry, self.leases, self.audit)
        self.state = ObservableState()
        self.feed = EventFeed(state=self.state, leases=self.leases, bus=self.bus, pier_ids=lambda: [PIER])
        self.surface = ControlSurfaceService(self.registry, self.leases, self.gateway, self.state,
                                             lambda: [{"id": PIER, "name": "East Pier"}], feed=self.feed)
        self.engine_running = True
        self.peers = PeerStore()
        self.pairing = PairingService(self.peers)
        self.certs = CertificateStore(MemorySecretStore(), "test-master")
        self.clients = ClientRegistry(self.peers, self.pairing, lambda: self.leases, self.certs.fingerprint)
        self.server = LinkServer(self.peers, self.pairing, self.certs, self.audit,
                                 handshake=Handshake(lambda: Versions("test", "1", 0, PROTOCOL_VERSION)))

        class NoDiscovery:
            async def advertise(self, *args):
                pass

            async def withdraw(self):
                pass

        self.server.discovery = NoDiscovery()
        self.runtime = Runtime(None, self.peers, self.clients, lambda: self.surface if self.engine_running else None,
                               lambda: "Test Master")

    async def start(self) -> None:
        await self.server.start("127.0.0.1", 0)
        self.runtime.install(self.server)

    async def stop(self) -> None:
        await self.runtime.pump.stop()
        await self.server.stop()
        self.feed.close()
