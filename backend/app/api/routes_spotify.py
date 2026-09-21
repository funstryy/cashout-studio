"""Spotify: listening history in, LoRA dataset out.

Audio never comes from Spotify - it cannot, and see spotify.py for why. What
crosses this boundary is what the user listens to and what Spotify calls it;
the audio is theirs already, sitting on their own disk.

The connection is per profile, because a profile is whose work is whose and a
listening history is about as personal as this app gets. The token lives in
the same SQLite file as everything else, which is to say: readable by anyone
at this machine. That is stated plainly in the UI rather than dressed up.
"""
from __future__ import annotations

import asyncio
import shutil
import time
import webbrowser
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from .. import db, spotify
from ..config import ACE_STEP_DIR
from .routes_users import current_user_id

router = APIRouter(prefix="/api/spotify", tags=["spotify"])

# One in-flight sign-in at a time, keyed by profile. Kept in memory on
# purpose: a half-finished OAuth handshake should not survive a restart.
_pending: dict[int, spotify.PendingAuth] = {}
_PENDING_TTL = 600.0


def _redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}{spotify.REDIRECT_PATH}"


def _account(user_id: int) -> Optional[dict]:
    return db.get_spotify_account(user_id)


async def _access_token(user_id: int) -> tuple[str, str]:
    """A usable token, refreshed if it has aged out. Returns (token, client_id)."""
    account = _account(user_id)
    if not account or not account.get("refresh_token"):
        raise HTTPException(status_code=401, detail="not connected to Spotify")

    if float(account.get("expires_at") or 0) > time.time():
        return account["access_token"], account["client_id"]

    try:
        refreshed = await spotify.refresh_token(account["client_id"], account["refresh_token"])
    except spotify.SpotifyError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None

    # Spotify does not always return a new refresh token; keep the old one
    # when it does not, or the next refresh has nothing to present.
    db.save_spotify_tokens(
        user_id,
        access_token=refreshed["access_token"],
        refresh_token=refreshed.get("refresh_token") or account["refresh_token"],
        expires_at=refreshed["expires_at"],
    )
    return refreshed["access_token"], account["client_id"]


# ------------------------------------------------------------------ connection

@router.get("/status")
async def status(user_id: int = Depends(current_user_id)):
    account = _account(user_id)
    return {
        "configured": bool(account and account.get("client_id")),
        "connected": bool(account and account.get("refresh_token")),
        "display_name": (account or {}).get("display_name"),
        "client_id": (account or {}).get("client_id"),
        "scopes": list(spotify.SCOPES),
        "redirect_path": spotify.REDIRECT_PATH,
    }


@router.post("/config")
async def save_config(client_id: str = Body(..., embed=True), user_id: int = Depends(current_user_id)):
    """Each profile brings its own client ID.

    Not one shipped with the studio: since February 2026 a Development Mode
    client ID authorises at most five users, so an embedded one would cap the
    whole product at five people. This is also why there is no client secret
    anywhere - the flow is PKCE, which is what a public client should use.
    """
    clean = (client_id or "").strip()
    if not clean or len(clean) < 10:
        raise HTTPException(status_code=400, detail="that does not look like a Spotify client ID")
    db.save_spotify_client_id(user_id, clean)
    return await status(user_id)


@router.get("/login")
async def login(port: int = Query(9000), user_id: int = Depends(current_user_id)):
    account = _account(user_id)
    if not account or not account.get("client_id"):
        raise HTTPException(status_code=400, detail="add your Spotify client ID first")

    url, pending = spotify.start_authorization(account["client_id"], _redirect_uri(port))
    _pending[user_id] = pending

    # Opened in the system browser, not in the studio's WebView. OAuth in an
    # embedded webview is both discouraged and, increasingly, refused - and
    # the user's Spotify session already lives in their real browser, so this
    # is usually one click rather than a password.
    opened = False
    try:
        opened = webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001 - the link is returned either way
        opened = False

    return {"url": url, "redirect_uri": pending.redirect_uri, "opened": opened}


@router.get("/callback", response_class=HTMLResponse)
async def callback(code: str = "", state: str = "", error: str = ""):
    """Where Spotify sends the browser back.

    Returns a page rather than JSON because a person is looking at it - this
    is the one endpoint in the app whose audience is a browser tab the user
    opened themselves, and it has to tell them to go back to the studio.
    """
    if error:
        return _page("Sign-in cancelled", f"Spotify reported: {error}", ok=False)

    pending_user = next(
        (uid for uid, p in _pending.items()
         if p.state == state and time.time() - p.created_at < _PENDING_TTL),
        None,
    )
    if pending_user is None:
        return _page(
            "That sign-in has expired",
            "Start it again from the LoRA page. (A sign-in is only valid for ten minutes, "
            "and only in the studio window that began it.)",
            ok=False,
        )

    pending = _pending.pop(pending_user)
    try:
        tokens = await spotify.exchange_code(code, pending)
    except spotify.SpotifyError as exc:
        return _page("Spotify refused the sign-in", str(exc), ok=False)

    display_name = ""
    try:
        client = spotify.SpotifyClient(tokens["access_token"])
        async with httpx.AsyncClient(timeout=30) as http:
            me = await client.me(http)
        display_name = me.get("display_name") or me.get("id") or ""
    except Exception:  # noqa: BLE001 - a missing name is not worth failing over
        display_name = ""

    db.save_spotify_tokens(
        pending_user,
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token", ""),
        expires_at=tokens["expires_at"],
        display_name=display_name,
    )
    return _page("Connected", f"Signed in as {display_name or 'your account'}. "
                              "You can close this tab and go back to Cashout Studio.", ok=True)


def _page(title: str, body: str, ok: bool) -> HTMLResponse:
    colour = "#1db954" if ok else "#e0245e"
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><title>{title}</title>
<body style="margin:0;display:flex;align-items:center;justify-content:center;height:100vh;
background:#0f1115;color:#e8e8ea;font:15px/1.5 system-ui,sans-serif">
<div style="max-width:30rem;padding:2rem;text-align:center">
<div style="width:3rem;height:3rem;border-radius:999px;background:{colour};margin:0 auto 1.25rem"></div>
<h1 style="font-size:1.25rem;margin:0 0 .5rem">{title}</h1>
<p style="margin:0;color:#9aa0a6">{body}</p></div></body>""",
        status_code=200,
    )


@router.post("/disconnect")
async def disconnect(user_id: int = Depends(current_user_id)):
    db.clear_spotify_tokens(user_id)
    _pending.pop(user_id, None)
    return await status(user_id)


# ------------------------------------------------------------------ listening

@router.get("/listening")
async def listening(
    source: str = Query("recent", pattern="^(recent|top_short|top_medium|top_long|saved)$"),
    with_genres: bool = Query(True),
    user_id: int = Depends(current_user_id),
):
    """What this profile has been listening to, with genres where possible.

    Genres come from one request per artist, because Spotify removed the batch
    artist endpoint in February 2026. They are also the part most likely to be
    refused outright, so the response says whether they arrived rather than
    quietly returning empty lists that look like "this artist has no genre".
    """
    token, _ = await _access_token(user_id)
    client = spotify.SpotifyClient(token)

    try:
        async with httpx.AsyncClient(timeout=30) as http:
            if source == "recent":
                raw = await client.recently_played(http)
            elif source == "saved":
                raw = await client.saved_tracks(http)
            else:
                ranges = {"top_short": "short_term", "top_medium": "medium_term", "top_long": "long_term"}
                raw = await client.top_tracks(http, time_range=ranges[source])

            # Deduplicate, keeping play counts: a track heard five times today
            # says more about someone's taste than one heard once.
            tracks: dict[str, spotify.ListeningTrack] = {}
            order: list[str] = []
            for index, item in enumerate(raw):
                track = spotify.to_listening_track(item)
                if not track.track_id:
                    continue
                if track.track_id in tracks:
                    tracks[track.track_id].plays += 1
                    continue
                track.plays = 1
                if source != "recent":
                    track.rank = index + 1
                tracks[track.track_id] = track
                order.append(track.track_id)

            genre_status = "skipped"
            if with_genres and tracks:
                genre_status = await _attach_genres(http, client, raw, tracks)
    except spotify.SpotifyRateLimited as exc:
        raise HTTPException(status_code=429, detail=f"Spotify rate limit - retry in {exc.retry_after:.0f}s")
    except spotify.SpotifyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return {
        "source": source,
        "genre_status": genre_status,
        "tracks": [tracks[tid].as_dict() for tid in order],
    }


async def _attach_genres(http: httpx.AsyncClient, client: spotify.SpotifyClient,
                         raw: list[dict], tracks: dict[str, spotify.ListeningTrack]) -> str:
    """Fills in genres, and reports honestly whether it managed to.

    Returns "ok", "partial" or "unavailable". The distinction matters: with no
    genres the captions fall back to artist and year, which still trains, but
    the user should know that is what they are getting rather than wondering
    why the labels got worse.
    """
    by_artist: dict[str, list[str]] = {}
    for artist_id in spotify.artist_ids(raw):
        try:
            artist = await client.artist(http, artist_id)
        except spotify.SpotifyRateLimited as exc:
            await asyncio.sleep(min(exc.retry_after, 5))
            continue
        genres = artist.get("genres") or []
        if genres:
            by_artist[artist_id] = genres

    if not by_artist:
        return "unavailable"

    matched = 0
    for item in raw:
        track = tracks.get(item.get("id") or "")
        if not track:
            continue
        collected: list[str] = []
        for artist in item.get("artists", []):
            collected.extend(by_artist.get(artist.get("id") or "", []))
        if collected:
            seen: set[str] = set()
            track.genres = [g for g in collected if not (g in seen or seen.add(g))][:5]
            matched += 1

    return "ok" if matched == len(tracks) else "partial"


# ------------------------------------------------------------------ matching

@router.post("/match")
async def match(body: dict = Body(...), user_id: int = Depends(current_user_id)):
    """Pairs listening history against audio already on this machine.

    Two sources are searched: the studio's own track library, and a folder the
    user points at. Neither is Spotify - that is the whole point. What comes
    back is split into what can be trained on now and what cannot, because
    "you listen to this but do not own the audio" is information the user can
    act on, and hiding it would make the dataset look thinner than it is for
    no visible reason.
    """
    tracks = [
        spotify.ListeningTrack(
            track_id=t.get("track_id", ""),
            title=t.get("title", ""),
            artists=t.get("artists") or ([t["artist"]] if t.get("artist") else []),
            album=t.get("album", ""),
            year=t.get("year", ""),
            genres=t.get("genres") or [],
            plays=int(t.get("plays") or 0),
        )
        for t in (body.get("tracks") or [])
    ]
    if not tracks:
        raise HTTPException(status_code=400, detail="no tracks to match")

    threshold = float(body.get("threshold") or 0.6)
    candidates: list[spotify.LocalAudio] = []

    # Several folders, not one. Measured on a real collection: the files worth
    # training on were spread over four directories on two drives, with the
    # other fifteen thousand audio files being drum kits, synth multisamples
    # and game assets. A single box would have meant either missing most of
    # it or pointing at a root and dragging all that in.
    folders = [str(f).strip() for f in (body.get("folders") or []) if str(f).strip()]
    single = (body.get("folder") or "").strip()
    if single and single not in folders:
        folders.append(single)

    for folder in folders:
        path = Path(folder)
        if not path.is_dir():
            raise HTTPException(status_code=400, detail=f"no such folder: {folder}")
        candidates.extend(spotify.scan_folder(path))

    if body.get("include_library", True):
        for row in db.list_tracks(None, user_id):
            audio_path = db.resolve_media_path(row["audio_path"])
            if audio_path:
                candidates.append(
                    spotify.LocalAudio(path=audio_path, label=row["title"] or audio_path.stem,
                                       source="library")
                )

    if not candidates:
        raise HTTPException(
            status_code=400,
            detail="nothing to match against - pick a folder of audio, or import tracks first",
        )

    matched, missing = [], []
    used: set[str] = set()
    for track in tracks:
        # A file can only back one track; without this, a generically-named
        # file wins every loosely-matching title in the list.
        available = [c for c in candidates if str(c.path) not in used]
        best, score = spotify.best_match(track, available, threshold)
        entry = track.as_dict()
        if best:
            used.add(str(best.path))
            matched.append({**entry, "local": best.as_dict(), "score": round(score, 3)})
        else:
            missing.append({**entry, "best_score": round(score, 3)})

    return {
        "matched": matched,
        "missing": missing,
        "searched": len(candidates),
        "threshold": threshold,
    }


# ------------------------------------------------------------------ dataset

@router.post("/build-dataset")
async def build_dataset(body: dict = Body(...), user_id: int = Depends(current_user_id)):
    """Copies the matched audio into an ACE-Step dataset and writes captions.

    The captions are the reason this feature earns its place. ACE-Step's
    dataset API keeps its own copy in state that a re-scan rebuilds, so they
    are written beside the audio as well - a user who rescans should not
    silently lose everything Spotify told us.
    """
    name = spotify_dataset_name(body.get("dataset_name") or "")
    matched = body.get("matched") or []
    if not matched:
        raise HTTPException(status_code=400, detail="nothing matched to build from")

    trigger = (body.get("trigger") or "").strip()
    include_artist = bool(body.get("include_artist", True))
    style_tags = [str(tag) for tag in (body.get("style_tags") or []) if str(tag).strip()]

    dataset_dir = ACE_STEP_DIR / "datasets" / name
    dataset_dir.mkdir(parents=True, exist_ok=True)

    captions: dict[str, str] = {}
    copied: list[dict] = []
    for entry in matched:
        source = Path((entry.get("local") or {}).get("path") or "")
        if not source.is_file():
            continue
        track = spotify.ListeningTrack(
            track_id=entry.get("track_id", ""),
            title=entry.get("title", ""),
            artists=entry.get("artists") or ([entry["artist"]] if entry.get("artist") else []),
            album=entry.get("album", ""),
            year=entry.get("year", ""),
            genres=entry.get("genres") or [],
        )
        dest = dataset_dir / source.name
        if not dest.exists():
            shutil.copy2(source, dest)
        caption = spotify.build_caption(track, trigger=trigger, include_artist=include_artist,
                                        style_tags=style_tags)
        captions[dest.name] = caption
        copied.append({"file": dest.name, "caption": caption, "title": track.title})

    if not copied:
        raise HTTPException(status_code=400, detail="none of the matched files could be read")

    spotify.write_captions(dataset_dir, captions)
    return {
        "dataset_name": name,
        # Relative, because ACE-Step's own path-safety check requires the
        # audio dir to sit inside its datasets folder.
        "audio_dir": f"datasets/{name}",
        "absolute_dir": str(dataset_dir),
        "files": copied,
    }


def spotify_dataset_name(raw: str) -> str:
    import re
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", raw[:60]).strip("_")
    return clean or "spotify_lora"


@router.get("/captions")
async def captions(dataset_name: str = Query(...)):
    """The captions written for a dataset, so the LoRA page can apply them
    after ACE-Step rescans the folder and forgets them."""
    dataset_dir = ACE_STEP_DIR / "datasets" / spotify_dataset_name(dataset_name)
    return {"dataset_name": dataset_name, "captions": spotify.read_captions(dataset_dir)}
