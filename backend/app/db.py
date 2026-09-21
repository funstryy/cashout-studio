"""Shared track storage for both models: one SQLite DB (distinguished by a
`model` column) plus generated files split into a subfolder per model under
DATA_DIR/files. Centralizing this here (instead of relying on each model's
own, separate storage - YuE2's built-in history.db, ACE-Step's ephemeral temp
dir) is what makes "one project, one place for everything it generated" true.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR

DB_PATH = DATA_DIR / "aicollector.db"
FILES_DIR = DATA_DIR / "files"

_db: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode = WAL;")
        _db.execute("PRAGMA synchronous = NORMAL;")
        _db.execute("PRAGMA foreign_keys = ON;")
        _db.execute("PRAGMA busy_timeout = 5000;")
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                lyrics TEXT NOT NULL DEFAULT '',
                seed INTEGER,
                duration_ms REAL,
                wall_ms REAL,
                params_json TEXT NOT NULL DEFAULT '{}',
                audio_path TEXT NOT NULL,
                abc_path TEXT
            )
            """
        )
        _db.commit()
        cols = {r["name"] for r in _db.execute("PRAGMA table_info(tracks)").fetchall()}
        if "stems_json" not in cols:
            _db.execute("ALTER TABLE tracks ADD COLUMN stems_json TEXT")
            _db.commit()
        if "mix_settings_json" not in cols:
            _db.execute("ALTER TABLE tracks ADD COLUMN mix_settings_json TEXT")
            _db.commit()
        if "midi_json" not in cols:
            _db.execute("ALTER TABLE tracks ADD COLUMN midi_json TEXT")
            _db.commit()
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT 'Untitled project',
                data_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        _db.commit()
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        _db.commit()
        # Profiles, not accounts: this is one machine's studio, so a profile
        # separates whose work is whose. It is deliberately not a security
        # boundary - anyone at this machine can pick any profile, and the
        # database is a plain file either way. A password box here would imply
        # protection that a local app cannot provide.
        if _db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
            _db.execute(
                "INSERT INTO users (name, created_at) VALUES (?, ?)",
                ("Studio", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
            _db.commit()

        # Existing work predates profiles, so it belongs to the first one
        # rather than becoming invisible the moment profiles arrive.
        first_user = _db.execute("SELECT MIN(id) AS id FROM users").fetchone()["id"]
        for table in ("tracks", "projects"):
            columns = {r["name"] for r in _db.execute(f"PRAGMA table_info({table})").fetchall()}
            if "user_id" not in columns:
                _db.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
                _db.execute(f"UPDATE {table} SET user_id = ?", (first_user,))
                _db.commit()

        _repair_moved_media(_db)

        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS spotify_accounts (
                user_id INTEGER PRIMARY KEY,
                client_id TEXT NOT NULL DEFAULT '',
                access_token TEXT NOT NULL DEFAULT '',
                refresh_token TEXT NOT NULL DEFAULT '',
                expires_at REAL NOT NULL DEFAULT 0,
                display_name TEXT NOT NULL DEFAULT '',
                connected_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        _db.commit()

        _db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_model_id ON tracks(model, id DESC);")
        _db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_created ON tracks(created_at DESC);")
        _db.execute("CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at DESC);")
        _db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_user ON tracks(user_id, id DESC);")
        _db.commit()
    return _db


def resolve_media_path(stored: str) -> Optional[Path]:
    """The file this row points at, wherever it actually is now.

    Rows hold absolute paths, which stop being true the moment the install
    moves - renaming the app folder did exactly that, and every track recorded
    before the rename pointed into a directory that no longer existed. The
    layout under files/ is stable even when its parent is not, so a stored
    path is re-rooted at this install's files directory before giving up.
    """
    path = Path(stored)
    if path.exists():
        return path
    parts = path.parts
    for i in range(len(parts) - 2, -1, -1):
        if parts[i].lower() == "files":
            candidate = FILES_DIR.joinpath(*parts[i + 1:])
            if candidate.exists():
                return candidate
    return None


def _repair_moved_media(conn: sqlite3.Connection) -> int:
    """Rewrites rows whose files have moved, once, at startup.

    Resolving on every read would work too, but would leave every row
    permanently wrong and quietly paper over it; a track's recorded location
    should be where the track is.
    """
    repaired = 0
    for row in conn.execute("SELECT id, audio_path, abc_path, stems_json FROM tracks").fetchall():
        updates: dict[str, str] = {}
        for column in ("audio_path", "abc_path"):
            stored = row[column]
            if not stored or Path(stored).exists():
                continue
            found = resolve_media_path(stored)
            if found:
                updates[column] = str(found)

        if row["stems_json"]:
            try:
                stems = json.loads(row["stems_json"])
            except (TypeError, ValueError):
                stems = None
            if isinstance(stems, dict):
                moved = False
                for name, stored in list(stems.items()):
                    if not stored or Path(stored).exists():
                        continue
                    found = resolve_media_path(stored)
                    if found:
                        stems[name] = str(found)
                        moved = True
                if moved:
                    updates["stems_json"] = json.dumps(stems, ensure_ascii=False)

        if updates:
            assignments = ", ".join(f"{column} = ?" for column in updates)
            conn.execute(
                f"UPDATE tracks SET {assignments} WHERE id = ?",
                (*updates.values(), row["id"]),
            )
            repaired += 1
    if repaired:
        conn.commit()
    return repaired


def get_spotify_account(user_id: int) -> Optional[dict]:
    row = get_db().execute(
        "SELECT * FROM spotify_accounts WHERE user_id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def save_spotify_client_id(user_id: int, client_id: str) -> None:
    """Stored per profile, not per machine.

    A listening history is about as personal as this app gets, and profiles
    already exist to keep one person's work from showing up in another's.
    Changing the client ID drops any existing tokens: they were issued by a
    different Spotify application and will not refresh against this one.
    """
    conn = get_db()
    existing = get_spotify_account(user_id)
    if existing and existing["client_id"] == client_id:
        return
    conn.execute(
        """
        INSERT INTO spotify_accounts (user_id, client_id) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            client_id = excluded.client_id,
            access_token = '', refresh_token = '', expires_at = 0, display_name = ''
        """,
        (user_id, client_id),
    )
    conn.commit()


def save_spotify_tokens(user_id: int, *, access_token: str, refresh_token: str,
                        expires_at: float, display_name: Optional[str] = None) -> None:
    conn = get_db()
    if display_name is None:
        conn.execute(
            """UPDATE spotify_accounts
               SET access_token = ?, refresh_token = ?, expires_at = ?
               WHERE user_id = ?""",
            (access_token, refresh_token, expires_at, user_id),
        )
    else:
        conn.execute(
            """UPDATE spotify_accounts
               SET access_token = ?, refresh_token = ?, expires_at = ?,
                   display_name = ?, connected_at = ?
               WHERE user_id = ?""",
            (access_token, refresh_token, expires_at, display_name,
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), user_id),
        )
    conn.commit()


def clear_spotify_tokens(user_id: int) -> None:
    """Disconnect. The client ID is kept - it is the user's own application
    registration and they will want it again if they reconnect."""
    conn = get_db()
    conn.execute(
        """UPDATE spotify_accounts
           SET access_token = '', refresh_token = '', expires_at = 0,
               display_name = '', connected_at = ''
           WHERE user_id = ?""",
        (user_id,),
    )
    conn.commit()


def model_dir(model: str) -> Path:
    d = FILES_DIR / model
    d.mkdir(parents=True, exist_ok=True)
    return d


def stems_dir(model: str, track_id: int) -> Path:
    return FILES_DIR / model / "stems" / str(track_id)


def midi_dir(model: str, track_id: int) -> Path:
    return FILES_DIR / model / "midi" / str(track_id)


def insert_track(
    *,
    model: str,
    title: str,
    lyrics: str,
    seed: Optional[int],
    duration_ms: Optional[float],
    wall_ms: Optional[float],
    params: dict[str, Any],
    audio_path: Path,
    abc_path: Optional[Path],
    user_id: Optional[int] = None,
) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO tracks (model, created_at, title, lyrics, seed, duration_ms, wall_ms, params_json, audio_path, abc_path, user_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            model,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            title,
            lyrics,
            seed,
            duration_ms,
            wall_ms,
            json.dumps(params, ensure_ascii=False),
            str(audio_path),
            str(abc_path) if abc_path else None,
            user_id if user_id is not None else default_user_id(),
        ),
    )
    db.commit()
    return cur.lastrowid


def list_tracks(model: Optional[str] = None, user_id: Optional[int] = None) -> list[sqlite3.Row]:
    db = get_db()
    clauses, params = [], []
    if model:
        clauses.append("model = ?")
        params.append(model)
    if user_id is not None:
        # Rows from before profiles existed have no owner; showing them to
        # everyone beats hiding someone's back catalogue.
        clauses.append("(user_id = ? OR user_id IS NULL)")
        params.append(user_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return db.execute(f"SELECT * FROM tracks{where} ORDER BY id DESC", params).fetchall()


# --- profiles --------------------------------------------------------------

def default_user_id() -> int:
    row = get_db().execute("SELECT MIN(id) AS id FROM users").fetchone()
    return row["id"] if row and row["id"] is not None else 1


def list_users() -> list[sqlite3.Row]:
    return get_db().execute("SELECT * FROM users ORDER BY id").fetchall()


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(name: str) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO users (name, created_at) VALUES (?, ?)",
        (name, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    db.commit()
    return cur.lastrowid


def delete_user(user_id: int) -> bool:
    """Removes the profile only. Their tracks stay on disk and in the
    database: deleting a profile should not quietly destroy recordings."""
    db = get_db()
    if not get_user(user_id) or len(list_users()) <= 1:
        return False
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return True


def get_track(track_id: int) -> Optional[sqlite3.Row]:
    db = get_db()
    return db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def update_track_title(track_id: int, title: str) -> bool:
    db = get_db()
    if not get_track(track_id):
        return False
    db.execute("UPDATE tracks SET title = ? WHERE id = ?", (title, track_id))
    db.commit()
    return True


def update_track_stems(track_id: int, stems: Optional[dict[str, str]]) -> None:
    db = get_db()
    db.execute(
        "UPDATE tracks SET stems_json = ? WHERE id = ?",
        (json.dumps(stems, ensure_ascii=False) if stems else None, track_id),
    )
    db.commit()


def get_mix_settings(track_id: int) -> Optional[dict]:
    row = get_track(track_id)
    return json.loads(row["mix_settings_json"]) if row and row["mix_settings_json"] else None


def update_track_mix_settings(track_id: int, settings: Optional[dict]) -> None:
    db = get_db()
    db.execute(
        "UPDATE tracks SET mix_settings_json = ? WHERE id = ?",
        (json.dumps(settings, ensure_ascii=False) if settings else None, track_id),
    )
    db.commit()


def delete_track_stems(track_id: int) -> bool:
    row = get_track(track_id)
    if not row or not row["stems_json"]:
        return False
    shutil.rmtree(stems_dir(row["model"], track_id), ignore_errors=True)
    update_track_stems(track_id, None)
    return True


def get_track_stems(track_id: int) -> dict[str, str]:
    row = get_track(track_id)
    return json.loads(row["stems_json"]) if row and row["stems_json"] else {}


def get_track_midi(track_id: int) -> dict[str, str]:
    row = get_track(track_id)
    return json.loads(row["midi_json"]) if row and row["midi_json"] else {}


def set_track_midi_entry(track_id: int, source: str, path: Optional[Path]) -> None:
    """Add or drop one source's .mid without touching the others - each source
    (full mix, and each stem) is transcribed by its own job."""
    current = get_track_midi(track_id)
    if path is None:
        current.pop(source, None)
    else:
        current[source] = str(path)
    db = get_db()
    db.execute(
        "UPDATE tracks SET midi_json = ? WHERE id = ?",
        (json.dumps(current, ensure_ascii=False) if current else None, track_id),
    )
    db.commit()


def delete_track_midi(track_id: int) -> bool:
    row = get_track(track_id)
    if not row or not row["midi_json"]:
        return False
    shutil.rmtree(midi_dir(row["model"], track_id), ignore_errors=True)
    db = get_db()
    db.execute("UPDATE tracks SET midi_json = NULL WHERE id = ?", (track_id,))
    db.commit()
    return True


def insert_project(*, name: str, data: dict) -> int:
    db = get_db()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur = db.execute(
        "INSERT INTO projects (created_at, updated_at, name, data_json) VALUES (?, ?, ?, ?)",
        (now, now, name, json.dumps(data, ensure_ascii=False)),
    )
    db.commit()
    return cur.lastrowid


def list_projects() -> list[sqlite3.Row]:
    db = get_db()
    return db.execute("SELECT id, created_at, updated_at, name FROM projects ORDER BY updated_at DESC").fetchall()


def get_project(project_id: int) -> Optional[sqlite3.Row]:
    db = get_db()
    return db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def update_project(project_id: int, *, name: Optional[str], data: Optional[dict]) -> None:
    db = get_db()
    row = get_project(project_id)
    if not row:
        return
    new_name = name if name is not None else row["name"]
    new_data_json = json.dumps(data, ensure_ascii=False) if data is not None else row["data_json"]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db.execute(
        "UPDATE projects SET name = ?, data_json = ?, updated_at = ? WHERE id = ?",
        (new_name, new_data_json, now, project_id),
    )
    db.commit()


def delete_project(project_id: int) -> bool:
    db = get_db()
    if not get_project(project_id):
        return False
    db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    db.commit()
    return True


def delete_track(track_id: int) -> bool:
    db = get_db()
    row = get_track(track_id)
    if not row:
        return False
    for p in (row["audio_path"], row["abc_path"]):
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
    if row["stems_json"]:
        shutil.rmtree(stems_dir(row["model"], track_id), ignore_errors=True)
    if row["midi_json"]:
        shutil.rmtree(midi_dir(row["model"], track_id), ignore_errors=True)
    db.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    db.commit()
    return True
