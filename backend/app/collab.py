"""Live collaboration over a LAN or a Radmin VPN.

The shape of the problem
------------------------
Two or three friends on one virtual network, editing one arrangement, with
no save step between one person's edit and another person seeing it. Radmin
puts everyone on the same subnet, so there is no NAT to traverse and no
relay to rent: one machine hosts, the others connect straight to it.

Why host-authoritative and not a CRDT
-------------------------------------
A CRDT would let everyone edit offline and merge perfectly, and it would be
several thousand lines plus a new mental model for every future change to
the project format. What this actually needs is much smaller: a handful of
people on a low-latency link, almost always working on *different* tracks.
So one machine owns the document, applies changes in the order it receives
them, and broadcasts the result. Simultaneous edits to the same lane resolve
last-writer-wins, which is wrong roughly never and cheap to reason about.

Sync is per lane rather than per document. Sending the whole project on
every drag would make two people editing different tracks clobber each
other constantly; sending individual operations would mean teaching this
module every edit the app can ever make. A lane is the unit people actually
divide work along, so diffing at that granularity gets the common case right
without knowing what an edit means.

Security
--------
This is the first thing in the studio that listens beyond loopback, and
everything already built assumes a trusted caller: the file browser reads
the disk, the track API deletes files, profiles are explicitly not a
security boundary. So a session carries a token, the token is required on
the socket, and a session cannot be started without one. Radmin networks are
shared with whoever has the network name and password - that is not a
trust boundary either.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# Colours assigned to peers in join order. Chosen to stay distinguishable on
# the dark timeline and to survive the most common colour blindness.
PEER_COLOURS = ["#24e1c0", "#38bdf8", "#f0b429", "#f472b6", "#a78bfa", "#34d399"]


@dataclass
class Peer:
    peer_id: str
    name: str
    colour: str
    websocket: Any
    is_host: bool = False
    playhead: float = 0.0
    joined_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "peer_id": self.peer_id,
            "name": self.name,
            "colour": self.colour,
            "is_host": self.is_host,
            "playhead": self.playhead,
        }


class Session:
    """One shared arrangement and everyone currently in it."""

    def __init__(self) -> None:
        self.active = False
        self.token = ""
        self.project_name = ""
        # The authoritative document. Lanes are held separately from the
        # scalars so a lane edit and a tempo change cannot overwrite one
        # another.
        self.lanes: dict[str, dict] = {}
        self.lane_order: list[str] = []
        self.scalars: dict[str, Any] = {}
        self.peers: dict[str, Peer] = {}
        self.revision = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ lifecycle

    def start(self, project_name: str) -> str:
        self.active = True
        self.token = secrets.token_urlsafe(18)
        self.project_name = project_name
        self.revision = 0
        return self.token

    def stop(self) -> None:
        self.active = False
        self.token = ""
        self.lanes.clear()
        self.lane_order.clear()
        self.scalars.clear()
        self.peers.clear()

    def check_token(self, token: str) -> bool:
        # compare_digest so a wrong token cannot be found a character at a
        # time by timing the rejection.
        return bool(self.token) and secrets.compare_digest(token, self.token)

    # ------------------------------------------------------------ document

    def seed(self, project: dict) -> None:
        """The host's project becomes the shared one when a session opens."""
        self.lane_order = [lane["id"] for lane in project.get("lanes", [])]
        self.lanes = {lane["id"]: lane for lane in project.get("lanes", [])}
        self.scalars = {k: v for k, v in project.items() if k != "lanes"}

    def snapshot(self) -> dict:
        return {
            **self.scalars,
            "lanes": [self.lanes[i] for i in self.lane_order if i in self.lanes],
        }

    def apply(self, patch: dict) -> dict:
        """Merges one peer's changes and returns what to broadcast.

        Only what the sender actually changed is touched. A peer that has not
        mentioned a lane leaves it exactly as it was, which is what keeps two
        people on two tracks from fighting.
        """
        changed: dict[str, Any] = {}

        lanes = patch.get("lanes")
        if isinstance(lanes, list):
            for lane in lanes:
                lane_id = lane.get("id")
                if not lane_id:
                    continue
                self.lanes[lane_id] = lane
                if lane_id not in self.lane_order:
                    self.lane_order.append(lane_id)
            changed["lanes"] = lanes

        removed = patch.get("removed_lanes")
        if isinstance(removed, list) and removed:
            for lane_id in removed:
                self.lanes.pop(lane_id, None)
                if lane_id in self.lane_order:
                    self.lane_order.remove(lane_id)
            changed["removed_lanes"] = removed

        order = patch.get("lane_order")
        if isinstance(order, list) and order:
            # Only ids we know about, so a stale client cannot resurrect a
            # lane somebody else deleted.
            self.lane_order = [i for i in order if i in self.lanes]
            changed["lane_order"] = self.lane_order

        scalars = patch.get("scalars")
        if isinstance(scalars, dict) and scalars:
            self.scalars.update(scalars)
            changed["scalars"] = scalars

        if changed:
            self.revision += 1
            changed["revision"] = self.revision
        return changed

    # ------------------------------------------------------------ peers

    def add_peer(self, name: str, websocket: Any, is_host: bool) -> Peer:
        peer_id = secrets.token_hex(6)
        colour = PEER_COLOURS[len(self.peers) % len(PEER_COLOURS)]
        peer = Peer(peer_id=peer_id, name=name or "Guest", colour=colour,
                    websocket=websocket, is_host=is_host)
        self.peers[peer_id] = peer
        return peer

    def remove_peer(self, peer_id: str) -> None:
        self.peers.pop(peer_id, None)

    def roster(self) -> list[dict]:
        return [peer.public() for peer in self.peers.values()]

    async def broadcast(self, message: dict, *, skip: Optional[str] = None) -> None:
        """Sends to everyone, dropping anyone whose socket has died.

        Failures are swallowed per peer rather than aborting the loop: one
        friend closing their laptop must not stop the others receiving the
        edit.
        """
        payload = json.dumps(message)
        dead: list[str] = []
        for peer_id, peer in list(self.peers.items()):
            if peer_id == skip:
                continue
            try:
                await peer.websocket.send_text(payload)
            except Exception:  # noqa: BLE001 - any socket error means gone
                dead.append(peer_id)
        for peer_id in dead:
            self.remove_peer(peer_id)
        if dead:
            await self.broadcast({"type": "peers", "peers": self.roster()})

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock


session = Session()


def lan_addresses() -> list[str]:
    """Addresses a friend could actually reach this machine on.

    Radmin hands out 26.x.x.x, which is the one worth showing first - if a
    Radmin adapter is up, that is almost certainly the address the session is
    meant to be reached on, and hunting for it in ipconfig is a step nobody
    should have to take.
    """
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address.startswith("127.") or address in found:
                continue
            found.append(address)
    except OSError:
        pass
    # 26.x is Radmin's range and goes first. 169.254.x is what Windows
    # assigns an adapter that failed to get a lease - it is listed last
    # rather than hidden, because a disconnected Radmin adapter looks
    # exactly like this and seeing it is a useful hint.
    found.sort(key=lambda a: (not a.startswith("26."), a.startswith("169.254."), a))
    return found


# --------------------------------------------------------------- access guard

# The port the studio is actually serving on. run_desktop picks it at launch
# and records it here so an invite can name a reachable address instead of
# guessing 9000.
served_port = 9000

# What a peer on the VPN may touch. Everything else in the studio - the file
# browser, track deletion, plugin scanning, the LoRA trainer - stays loopback
# only, because none of it was written with a hostile caller in mind and a
# Radmin network is not a trust boundary.
#
# Audio is on the list because it has to be: a shared arrangement references
# the host's library by URL, so a guest that cannot fetch those bytes sees the
# clips and hears silence.
# Written out rather than matched by suffix: a shared arrangement points at
# separated stems as often as at whole tracks, and guessing at the shape of
# those URLs got /api/tracks/5/stems/vocals wrong, which is a guest watching
# clips play in silence.
_REMOTE_GET_PATHS = (
    re.compile(r"^/api/tracks/\d+/(?:audio|mix)$"),
    re.compile(r"^/api/tracks/\d+/stems/[^/]+$"),
    re.compile(r"^/api/collab/[a-z]+$"),
)


def remote_request_allowed(method: str, path: str, token: str) -> bool:
    """Whether a request from off-machine may proceed.

    Default deny. A caller has to be in an open session, carry the session
    token, and be asking for one of a short list of read-only things.
    """
    if not active_and_open():
        return False
    if not check_session_token(token):
        return False
    if method not in ("GET", "HEAD"):
        return False
    # Audio and the session's own endpoints, nothing else: not
    # /api/tracks/<id>, which carries lyrics and prompts, and not the
    # collection listing, which is the whole library.
    return any(pattern.match(path) for pattern in _REMOTE_GET_PATHS)


def active_and_open() -> bool:
    return session.active and bool(session.token)


def check_session_token(token: str) -> bool:
    return bool(token) and session.check_token(token)


# ----------------------------------------------------------- the open socket

# The studio's own server stays bound to 127.0.0.1 for its whole life. When a
# session opens, a *second* listener goes up on the VPN address and comes
# down again when the session closes, so the only time anything is reachable
# from another machine is while the host has deliberately opened a session.
# A permanent 0.0.0.0 bind guarded by a check would work right up until the
# check has a bug in it.
_listener: Any = None
_listener_task: Any = None
listening_on: str = ""


async def open_listener(address: str) -> str:
    """Starts serving on one extra address. Returns it, or raises OSError."""
    global _listener, _listener_task, listening_on
    import uvicorn

    from .main import app

    await close_listener()
    config = uvicorn.Config(
        app,
        host=address,
        port=served_port,
        # The primary server already ran the lifespan. Running it again would
        # mean this listener's shutdown calls manager.stop_all() and kills the
        # model engines the moment a session ends.
        lifespan="off",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    # Server.serve() would install signal handlers and own the process. Only
    # the socket is wanted here, so the two steps it does before binding are
    # done by hand: without them startup() has no lifespan object to call.
    if not config.loaded:
        config.load()
    server.lifespan = config.lifespan_class(config)
    # Bind before returning, so "hosting started" in the UI means the address
    # is genuinely reachable rather than about to fail in a background task.
    await server.startup(sockets=None)
    if server.should_exit:
        raise OSError(f"could not listen on {address}:{served_port}")
    _listener = server
    _listener_task = asyncio.ensure_future(server.main_loop())
    listening_on = address
    return address


async def close_listener() -> None:
    global _listener, _listener_task, listening_on
    server, task = _listener, _listener_task
    _listener, _listener_task, listening_on = None, None, ""
    if server is None:
        return
    server.should_exit = True
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    try:
        await server.shutdown()
    except Exception:  # noqa: BLE001 - nothing useful to do if teardown fails
        pass
