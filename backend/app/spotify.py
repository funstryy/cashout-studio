"""Spotify as a source of taste and labels - never of audio.

What this is for
----------------
Training a LoRA on "the music I actually listen to" is a good idea that runs
straight into a wall: Spotify has no API that returns audio. Full tracks play
only through their DRM-protected SDK, and since 27 November 2024 a newly
registered app also loses the 30-second `preview_url`, `audio-features` and
`audio-analysis`. Ripping the stream would be both a terms-of-service breach
and DMCA circumvention, so it is not on the table at any price.

What is left is still worth having: Spotify knows *what* you listen to, in
order, over whatever window you ask for. So this module reads the listening
history, matches it against audio the user already has on disk, and builds a
dataset out of the overlap. The audio never comes from Spotify; only the
knowledge of which of your own files are worth training on.

Captions are assembled from what survives - artist, album, year - plus style
tags the user supplies once for the whole dataset. That last part started as
a fallback for the refused genre endpoint and turned out to be the better
input regardless: a style LoRA is trained toward one sound, and one accurate
set of tags beats fifty per-artist guesses. It also fixes the failure that
prompted this feature, where the local auto-labeller called a Flint rap
record "lofi, ebm, phonk".

Development Mode, as of February 2026
-------------------------------------
Spotify tightened this considerably and it shapes the whole design:

  * A Development Mode client ID needs a Premium account, is limited to one
    per developer, and authorises at most FIVE users. That is why the studio
    asks each person for their own client ID rather than shipping one: a
    single embedded ID would cap the entire product at five people.
  * The batch `GET /artists` endpoint is gone - artists are fetched one at a
    time, which is why this module caches them hard.
  * `GET /artists/{id}` is refused outright. This was measured against a real
    Development Mode app in September 2026, not inferred from the docs, which
    are ambiguous on the point - so genres do not arrive at all, and the
    captions fall back to the user's own style tags plus artist and year. The
    UI reports which of the two happened rather than quietly producing worse
    labels. If Spotify restores the endpoint, the genre path still works and
    will simply start filling in again.

Authentication is PKCE with a loopback redirect - the flow for a native app
that cannot keep a secret. There is no client secret anywhere in this file,
and there should never be one: it would be readable in the installed binary.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

# Only what this feature reads. Asking for more than is used is how a local
# app ends up holding a token that can rewrite somebody's library.
SCOPES = (
    "user-read-recently-played",
    "user-top-read",
    "user-library-read",
    "playlist-read-private",
    "user-read-currently-playing",
)

# Loopback, because Spotify requires HTTPS for every redirect except this one -
# and a native app has nowhere to host HTTPS. The port must match the studio's.
REDIRECT_PATH = "/api/spotify/callback"


class SpotifyError(RuntimeError):
    """Anything the user needs to be told about, in words they can act on."""


# ----------------------------------------------------------------- PKCE

@dataclass
class PendingAuth:
    verifier: str
    state: str
    client_id: str
    redirect_uri: str
    created_at: float = field(default_factory=time.time)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def start_authorization(client_id: str, redirect_uri: str) -> tuple[str, PendingAuth]:
    """The URL to send the browser to, and the secret to finish with.

    PKCE rather than a client secret: this app is installed on other people's
    machines, so any secret compiled into it is public the moment it ships.
    """
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(SCOPES),
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    pending = PendingAuth(verifier=verifier, state=state, client_id=client_id, redirect_uri=redirect_uri)
    return f"{AUTH_URL}?{query}", pending


async def exchange_code(code: str, pending: PendingAuth) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": pending.redirect_uri,
                "client_id": pending.client_id,
                "code_verifier": pending.verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    return _token_response(response)


async def refresh_token(client_id: str, refresh: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    return _token_response(response)


def _token_response(response: httpx.Response) -> dict:
    if response.status_code != 200:
        detail = ""
        try:
            body = response.json()
            detail = body.get("error_description") or body.get("error") or ""
        except ValueError:
            detail = response.text[:200]
        raise SpotifyError(f"Spotify refused the sign-in ({response.status_code}): {detail}")
    payload = response.json()
    payload["expires_at"] = time.time() + float(payload.get("expires_in", 3600)) - 60
    return payload


# ----------------------------------------------------------------- API client

class SpotifyClient:
    """A thin client over the endpoints Development Mode still allows.

    Deliberately thin. Every method here maps to one documented endpoint, so
    when Spotify removes another one - and on this track record they will -
    the failure is a single 403 in a single place rather than something
    subtle happening three layers down.
    """

    def __init__(self, access_token: str) -> None:
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._artist_cache: dict[str, dict] = {}

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> Optional[dict]:
        response = await client.get(f"{API_BASE}{path}", headers=self._headers, params=params or None)
        if response.status_code == 429:
            # Spotify's own backoff. Honour it rather than hammering: the
            # artist loop below can issue a lot of requests.
            wait = float(response.headers.get("Retry-After", "2"))
            raise SpotifyRateLimited(wait)
        if response.status_code in (401, 403):
            raise SpotifyError(
                f"Spotify refused {path} ({response.status_code}). Development Mode "
                "allows a restricted set of endpoints since February 2026, and only "
                "five authorised users per client ID - check the account is added as "
                "a user in your Spotify dashboard."
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def me(self, client: httpx.AsyncClient) -> dict:
        return await self._get(client, "/me") or {}

    async def recently_played(self, client: httpx.AsyncClient, limit: int = 50) -> list[dict]:
        payload = await self._get(client, "/me/player/recently-played", limit=min(limit, 50))
        return [item["track"] for item in (payload or {}).get("items", []) if item.get("track")]

    async def top_tracks(self, client: httpx.AsyncClient, time_range: str = "medium_term",
                         limit: int = 50) -> list[dict]:
        payload = await self._get(
            client, "/me/top/tracks", time_range=time_range, limit=min(limit, 50)
        )
        return list((payload or {}).get("items", []))

    async def saved_tracks(self, client: httpx.AsyncClient, limit: int = 50) -> list[dict]:
        payload = await self._get(client, "/me/tracks", limit=min(limit, 50))
        return [item["track"] for item in (payload or {}).get("items", []) if item.get("track")]

    async def artist(self, client: httpx.AsyncClient, artist_id: str) -> dict:
        """One artist, cached.

        Cached because the batch endpoint was removed in February 2026: a
        fifty-track history can touch sixty artists, and without this that is
        sixty requests every time the page is opened.
        """
        if artist_id in self._artist_cache:
            return self._artist_cache[artist_id]
        try:
            payload = await self._get(client, f"/artists/{artist_id}") or {}
        except SpotifyError:
            # Genres are best-effort by design - see the module docstring.
            payload = {}
        self._artist_cache[artist_id] = payload
        return payload


class SpotifyRateLimited(RuntimeError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited, retry in {retry_after:.0f}s")
        self.retry_after = retry_after


# ----------------------------------------------------------------- normalising

@dataclass
class ListeningTrack:
    """One track as the studio cares about it, not as Spotify returns it."""
    track_id: str
    title: str
    artists: list[str]
    album: str
    year: str
    genres: list[str]
    plays: int = 0
    rank: Optional[int] = None

    @property
    def artist(self) -> str:
        return self.artists[0] if self.artists else ""

    def as_dict(self) -> dict:
        return {
            "track_id": self.track_id,
            "title": self.title,
            "artist": self.artist,
            "artists": self.artists,
            "album": self.album,
            "year": self.year,
            "genres": self.genres,
            "plays": self.plays,
            "rank": self.rank,
        }


def _year_of(album: dict) -> str:
    date = (album or {}).get("release_date") or ""
    return date[:4] if len(date) >= 4 else ""


def to_listening_track(raw: dict) -> ListeningTrack:
    album = raw.get("album") or {}
    return ListeningTrack(
        track_id=raw.get("id") or "",
        title=(raw.get("name") or "").strip(),
        artists=[a.get("name", "").strip() for a in raw.get("artists", []) if a.get("name")],
        album=(album.get("name") or "").strip(),
        year=_year_of(album),
        genres=[],
    )


def artist_ids(raw_tracks: Iterable[dict]) -> list[str]:
    seen: list[str] = []
    for raw in raw_tracks:
        for artist in raw.get("artists", []):
            artist_id = artist.get("id")
            if artist_id and artist_id not in seen:
                seen.append(artist_id)
    return seen


# ----------------------------------------------------------------- matching

# Things that appear in downloaded filenames and never in a Spotify title.
_NOISE = re.compile(
    r"\b(official|video|audio|lyrics?|lyric|hd|hq|mv|m/?v|visualizer|visualiser|"
    r"explicit|clean|remaster(?:ed)?|prod(?:\.|uced)?\s*by|type\s*beat|free|"
    r"instrumental|full\s*song|music\s*video)\b",
    re.IGNORECASE,
)
_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
# Unicode-aware: stripping to ASCII collapsed "Деревенский Trap" to "trap",
# which then matched every drum sample with "trap" in its name.
_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Strip a title down to the words that carry identity.

    Filenames on disk carry a lot that Spotify's metadata never does -
    "[Official Video]", "(prod. by X)", underscores, the uploader's name. What
    survives here is roughly the set of words a person would say out loud.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("_", " ").replace("&", " and ")
    text = _BRACKETS.sub(" ", text)
    text = _NOISE.sub(" ", text)
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def tokens(text: str) -> set[str]:
    # One-letter tokens are noise, not signal.
    return {word for word in normalise(text).split() if len(word) > 1}


def match_score(track: ListeningTrack, candidate_name: str) -> float:
    """How strongly a local file looks like this track, from 0 to 1.

    Title and artist are scored separately, because they fail differently: a
    filename often omits the artist entirely ("never ending run.mp3"), while
    two different songs by the same artist share every artist token.

    Two guards, both added after measuring against a real 1,751-file
    collection that was mostly drum kits and synth presets:

      * A title of one or two words cannot identify a file on its own. "Roses"
        is wholly contained in "MA Bells on a Bed of Roses", and "Trap" in
        every trap sample ever shipped. Short titles must have the artist
        agreeing or they do not count at all.
      * A candidate is penalised for words the track cannot account for.
        Matching two tokens out of three is a different claim from matching
        two out of eleven, and pure recall cannot tell those apart.
    """
    candidate = tokens(candidate_name)
    title = tokens(track.title)
    if not candidate or not title:
        return 0.0

    artist = tokens(" ".join(track.artists))
    title_hits = len(title & candidate) / len(title)
    artist_hits = len(artist & candidate) / len(artist) if artist else 0.0

    if len(title) < 3 and artist_hits < 0.5:
        return 0.0

    if title_hits >= 0.999:
        base = 0.75 + 0.25 * artist_hits
    elif title_hits >= 0.6 and artist_hits >= 0.5:
        base = 0.55 + 0.2 * title_hits
    else:
        base = title_hits * 0.5 * (1.0 + artist_hits)

    # How much of the filename this track explains at all.
    explained = len((title | artist) & candidate) / len(candidate)
    return round(base * (0.55 + 0.45 * explained), 4)


@dataclass
class LocalAudio:
    path: Path
    label: str      # what to match against: filename stem, or a track title
    source: str     # "library" or "folder", for the UI

    def as_dict(self) -> dict:
        return {"path": str(self.path), "label": self.label, "source": self.source}


AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wma"}


def scan_folder(folder: Path, limit: int = 5000) -> list[LocalAudio]:
    found: list[LocalAudio] = []
    if not folder.is_dir():
        return found
    for path in folder.rglob("*"):
        if len(found) >= limit:
            break
        if path.is_file() and path.suffix.lower() in AUDIO_EXT:
            found.append(LocalAudio(path=path, label=path.stem, source="folder"))
    return found


def best_match(track: ListeningTrack, candidates: list[LocalAudio],
               threshold: float) -> tuple[Optional[LocalAudio], float]:
    best: Optional[LocalAudio] = None
    best_score = 0.0
    for candidate in candidates:
        score = match_score(track, candidate.label)
        if score > best_score:
            best, best_score = candidate, score
    if best_score < threshold:
        return None, best_score
    return best, best_score


# ----------------------------------------------------------------- captions

def build_caption(track: ListeningTrack, trigger: str = "", include_artist: bool = True,
                  style_tags: Optional[list[str]] = None) -> str:
    """What the LoRA is told this audio is.

    Ordered most-specific-first, because that is the end a caption gets read
    from. The trigger word, when there is one, goes first: it is the handle
    the user will type in a prompt to summon this style, and it has to bind to
    everything else here rather than to one genre among several.

    `style_tags` is the user's own description of the dataset, and it exists
    because Spotify's Development Mode refuses the artist endpoint that
    carries genres - measured, not assumed. For a style LoRA this is arguably
    the better input anyway: every sample in the set is being trained toward
    one sound, and one accurate set of tags beats fifty per-artist guesses.
    """
    parts: list[str] = []
    if trigger.strip():
        parts.append(trigger.strip())
    parts.extend(tag.strip() for tag in (style_tags or []) if tag.strip())
    parts.extend(track.genres)
    if include_artist and track.artist:
        parts.append(f"{track.artist} type beat")
    if track.year:
        parts.append(track.year)
    if not parts:
        # Nothing from Spotify survived - say something true rather than
        # writing an empty caption that trains on nothing.
        parts.append(track.title or "untitled")
    # Order-preserving dedupe: genres and the artist name often overlap.
    seen: set[str] = set()
    unique = [p for p in parts if not (p.lower() in seen or seen.add(p.lower()))]
    return ", ".join(unique)


def captions_path(dataset_dir: Path) -> Path:
    return dataset_dir / "spotify_captions.json"


def write_captions(dataset_dir: Path, captions: dict[str, str]) -> Path:
    """Kept beside the audio so the mapping survives a re-scan.

    ACE-Step's dataset API holds captions in its own state, which is rebuilt
    every time a folder is scanned. Writing them here too means a user who
    rescans does not silently lose everything Spotify told us.
    """
    path = captions_path(dataset_dir)
    path.write_text(json.dumps(captions, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_captions(dataset_dir: Path) -> dict[str, str]:
    path = captions_path(dataset_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}
