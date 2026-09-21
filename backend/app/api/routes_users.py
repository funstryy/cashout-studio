"""Local profiles.

Several people share one machine and one studio install; a profile keeps
whose tracks are whose. This is not authentication and does not pretend to
be: there is no password, the database is a file on a disk everyone at this
machine can read, and switching profiles is a click. A login form here would
imply a protection that a local app has no way to enforce.

The current profile travels as an X-User-Id header rather than a session,
because there is no session to have - the "server" is this machine.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException

from .. import db

router = APIRouter(prefix="/api/users", tags=["users"])

USER_HEADER = "x-user-id"


def current_user_id(x_user_id: Optional[str] = Header(default=None)) -> int:
    """Whoever the UI says is using the app, falling back to the first
    profile so that every existing caller keeps working unchanged."""
    if x_user_id:
        try:
            user_id = int(x_user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="X-User-Id must be a number") from None
        if db.get_user(user_id):
            return user_id
    return db.default_user_id()


def _as_dict(row) -> dict:
    return {"id": row["id"], "name": row["name"], "created_at": row["created_at"]}


@router.get("")
async def list_users():
    return {"users": [_as_dict(u) for u in db.list_users()], "default_id": db.default_user_id()}


@router.post("")
async def create_user(name: str = Body(..., embed=True)):
    clean = name.strip()[:40]
    if not clean:
        raise HTTPException(status_code=400, detail="a profile needs a name")
    if any(u["name"].lower() == clean.lower() for u in db.list_users()):
        raise HTTPException(status_code=409, detail=f"'{clean}' already exists")
    user_id = db.create_user(clean)
    return _as_dict(db.get_user(user_id))


@router.delete("/{user_id}")
async def delete_user(user_id: int):
    if not db.delete_user(user_id):
        raise HTTPException(
            status_code=400,
            detail="cannot remove this profile - it either doesn't exist or is the last one",
        )
    return {"deleted": True}
