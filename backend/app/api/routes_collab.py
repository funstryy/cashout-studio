"""Hosting and joining a live session.

The host's studio is the server. A guest's studio talks to it over one
WebSocket: it sends what it changed, it receives what everybody else
changed, and nothing is ever saved for the other side to see it.

Two things travel besides the arrangement - the playhead and each peer's
selection - because without them a shared timeline is disorienting. You
cannot tell who moved a clip, or whether the person you are talking to is
looking at bar 4 or bar 40.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

from .. import collab
from ..collab import session

router = APIRouter(prefix="/api/collab", tags=["collab"])


def _invite(token: str) -> dict:
    addresses = collab.lan_addresses()
    return {
        "token": token,
        "port": collab.served_port,
        "addresses": addresses,
        "listening_on": collab.listening_on,
        # One string to paste into a chat window. The host address comes
        # first so a guest never has to read the parts separately.
        "code": f"{collab.listening_on or (addresses[0] if addresses else '')}"
                f":{collab.served_port}#{token}" if (collab.listening_on or addresses) else "",
    }


def _state() -> dict:
    return {
        "active": session.active,
        "project_name": session.project_name,
        "peers": session.roster(),
        "revision": session.revision,
        **(_invite(session.token) if session.active else
           {"token": "", "port": collab.served_port, "listening_on": "",
            "addresses": collab.lan_addresses(), "code": ""}),
    }


@router.get("/status")
async def status():
    return _state()


@router.post("/host")
async def host(body: dict = Body(default={})):
    """Opens a session seeded with the host's current arrangement."""
    if session.active:
        # Restarting would invalidate the token guests are already using.
        raise HTTPException(status_code=409, detail="a session is already open")
    project = body.get("project")
    if not isinstance(project, dict):
        raise HTTPException(status_code=400, detail="send the project to share")
    addresses = collab.lan_addresses()
    address = (body.get("address") or "").strip() or (addresses[0] if addresses else "")
    if not address:
        raise HTTPException(
            status_code=409,
            detail="no network address to host on - start Radmin VPN, then try again",
        )
    if address not in addresses:
        raise HTTPException(status_code=400, detail=f"{address} is not an address of this machine")

    token = session.start((body.get("project_name") or "Untitled").strip()[:80])
    session.seed(project)
    try:
        await collab.open_listener(address)
    except OSError as exc:
        # Leaving a session "open" that nobody can reach would be worse than
        # failing here, so unwind it completely.
        session.stop()
        raise HTTPException(status_code=500, detail=f"could not open the port: {exc}") from None
    return _state()


@router.post("/stop")
async def stop():
    """Ends the session and disconnects everyone.

    Guests are told why before the socket closes - a session that simply
    stops responding looks identical to a network drop, and the two want
    very different reactions from the person staring at it.
    """
    if session.active:
        await session.broadcast({"type": "ended", "reason": "host closed the session"})
        for peer in list(session.peers.values()):
            try:
                await peer.websocket.close(code=1000)
            except Exception:  # noqa: BLE001 - already gone is fine
                pass
    session.stop()
    await collab.close_listener()
    return _state()


@router.websocket("/ws")
async def collab_socket(websocket: WebSocket, token: str = "", name: str = "", host: int = 0):
    """One peer, for as long as they stay connected.

    The token is checked before the handshake completes. Accepting first and
    closing after would give an unauthenticated caller a live socket on the
    host, however briefly.
    """
    if not collab.active_and_open():
        await websocket.close(code=4404)
        return
    if not collab.check_session_token(token):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    peer = session.add_peer(name=name[:24], websocket=websocket, is_host=bool(host))

    await websocket.send_text(json.dumps({
        "type": "welcome",
        "peer_id": peer.peer_id,
        "colour": peer.colour,
        "project_name": session.project_name,
        "project": session.snapshot(),
        "revision": session.revision,
        "peers": session.roster(),
    }))
    await session.broadcast({"type": "peers", "peers": session.roster()}, skip=peer.peer_id)
    await session.broadcast({
        "type": "chat", "system": True,
        "text": f"{peer.name} joined", "peer_id": peer.peer_id,
    }, skip=peer.peer_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message: Any = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            await _handle(peer, message)
    except WebSocketDisconnect:
        pass
    except (RuntimeError, asyncio.CancelledError):
        # The socket died mid-send, or the server is shutting down. Either
        # way the peer is gone and the roster has to reflect that.
        pass
    finally:
        session.remove_peer(peer.peer_id)
        if session.active:
            await session.broadcast({"type": "peers", "peers": session.roster()})
            await session.broadcast({
                "type": "chat", "system": True, "text": f"{peer.name} left",
                "peer_id": peer.peer_id,
            })


async def _handle(peer, message: dict) -> None:
    kind = message.get("type")

    if kind == "patch":
        # Serialised: two peers landing edits in the same millisecond must
        # not interleave inside apply(), or the revision counter and the
        # document disagree about what was sent.
        async with session.lock:
            changed = session.apply(message)
            if not changed:
                return
            changed["type"] = "patch"
            changed["peer_id"] = peer.peer_id
        await session.broadcast(changed, skip=peer.peer_id)
        return

    if kind == "cursor":
        # Not stored in history and not revisioned: a playhead moving 60
        # times a second is worth showing and never worth replaying.
        position = message.get("playhead")
        if isinstance(position, (int, float)):
            peer.playhead = float(position)
        await session.broadcast({
            "type": "cursor",
            "peer_id": peer.peer_id,
            "playhead": peer.playhead,
            "selected_clip": message.get("selected_clip"),
        }, skip=peer.peer_id)
        return

    if kind == "transport":
        await session.broadcast({
            "type": "transport",
            "peer_id": peer.peer_id,
            "playing": bool(message.get("playing")),
            "playhead": float(message.get("playhead") or 0.0),
        }, skip=peer.peer_id)
        return

    if kind == "chat":
        text = str(message.get("text") or "").strip()[:500]
        if text:
            await session.broadcast({
                "type": "chat", "peer_id": peer.peer_id,
                "name": peer.name, "colour": peer.colour, "text": text,
            })
        return

    if kind == "ping":
        await peer.websocket.send_text(json.dumps({"type": "pong"}))
