import argparse
import getpass
import hashlib
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from backend.database import connect, initialize_database
from backend.settings import DB_PATH

VALID_ROLES = {"ADMIN", "USER", "READ_ONLY"}
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,48}$")
PASSWORD_HASHER = PasswordHasher()
ACCESS_TOKEN_MINUTES = 15
REFRESH_TOKEN_DAYS = 14
_DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


class AccountConflictError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _token_secret() -> str:
    secret = os.getenv("AUTH_TOKEN_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("AUTH_TOKEN_SECRET must contain at least 32 characters")
    return secret


def validate_username(username: str) -> str:
    normalized = username.strip()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("Username must be 3-48 letters, digits, dots, underscores or hyphens")
    return normalized


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must contain 12-128 characters")
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError, VerificationError):
        return False


def _audit(
    connection: sqlite3.Connection,
    action: str,
    outcome: Literal["success", "failure"],
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
) -> None:
    connection.execute(
        "INSERT INTO audit_logs "
        "(actor_user_id, target_user_id, action, outcome, occurred_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (actor_user_id, target_user_id, action, outcome, _timestamp(_now())),
    )


def create_user(
    db_path: Path,
    username: str,
    password: str,
    role: str,
    *,
    actor_user_id: int | None = None,
) -> dict[str, object]:
    normalized_username = validate_username(username)
    if role not in VALID_ROLES:
        raise ValueError("Role must be ADMIN, USER or READ_ONLY")
    password_hash = hash_password(password)
    now = _timestamp(_now())
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT INTO auth_users "
                "(username, password_hash, role, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (normalized_username, password_hash, role, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise AccountConflictError("Username already exists") from exc
        user_id = int(cursor.lastrowid)
        _audit(connection, "user.create", "success", actor_user_id, user_id)
        connection.commit()
        return {"id": user_id, "username": normalized_username, "role": role, "active": True}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_audit_logs(
    db_path: Path,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, object]], int]:
    connection = connect(db_path)
    try:
        count = int(connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0])
        rows = connection.execute(
            "SELECT id, actor_user_id, target_user_id, action, outcome, occurred_at "
            "FROM audit_logs ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows], count
    finally:
        connection.close()


def list_ntfy_command_audit(
    db_path: Path,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, object]], int]:
    connection = connect(db_path)
    try:
        count = int(
            connection.execute("SELECT COUNT(*) FROM ntfy_command_audit").fetchone()[0]
        )
        rows = connection.execute(
            "SELECT a.id, u.username AS actor, a.command, a.status, a.error_code, "
            "a.result_summary, a.received_at, a.completed_at "
            "FROM ntfy_command_audit a LEFT JOIN auth_users u ON u.id = a.actor_user_id "
            "ORDER BY a.received_at DESC, a.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows], count
    finally:
        connection.close()


def bootstrap_admin(db_path: Path, username: str, password: str) -> dict[str, object]:
    normalized_username = validate_username(username)
    password_hash = hash_password(password)
    now = _timestamp(_now())
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone():
            raise ValueError("Users already exist; use create-user instead")
        cursor = connection.execute(
            "INSERT INTO auth_users "
            "(username, password_hash, role, created_at, updated_at) "
            "VALUES (?, ?, 'ADMIN', ?, ?)",
            (normalized_username, password_hash, now, now),
        )
        user_id = int(cursor.lastrowid)
        _audit(connection, "user.bootstrap", "success", user_id, user_id)
        connection.commit()
        return {
            "id": user_id,
            "username": normalized_username,
            "role": "ADMIN",
            "active": True,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_users(db_path: Path) -> list[dict[str, object]]:
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT id, username, role, active, created_at, updated_at "
            "FROM auth_users ORDER BY username COLLATE NOCASE"
        ).fetchall()
        return [
            {
                **dict(row),
                "active": bool(row["active"]),
            }
            for row in rows
        ]
    finally:
        connection.close()


def update_user(
    db_path: Path,
    user_id: int,
    *,
    role: str | None = None,
    active: bool | None = None,
    password: str | None = None,
    actor_user_id: int,
) -> dict[str, object] | None:
    if role is not None and role not in VALID_ROLES:
        raise ValueError("Role must be ADMIN, USER or READ_ONLY")
    if active is not None and not isinstance(active, bool):
        raise ValueError("Active must be a boolean")
    password_hash = hash_password(password) if password is not None else None
    updates: list[str] = []
    values: list[object] = []
    if role is not None:
        updates.append("role = ?")
        values.append(role)
    if active is not None:
        updates.append("active = ?")
        values.append(int(active))
    if password_hash is not None:
        updates.append("password_hash = ?")
        values.append(password_hash)
    if not updates:
        raise ValueError("At least one account field must be provided")
    updates.append("updated_at = ?")
    values.append(_timestamp(_now()))
    values.append(user_id)
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT role, active FROM auth_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if current is None:
            connection.rollback()
            return None
        if (
            current["role"] == "ADMIN"
            and current["active"]
            and (role is not None and role != "ADMIN" or active is False)
        ):
            active_admins = connection.execute(
                "SELECT COUNT(*) FROM auth_users "
                "WHERE role = 'ADMIN' AND active = 1"
            ).fetchone()[0]
            if active_admins <= 1:
                raise ValueError("Cannot remove or disable the last active administrator")
        cursor = connection.execute(
            f"UPDATE auth_users SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            connection.rollback()
            return None
        if active is False or password_hash is not None:
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (_timestamp(_now()), user_id),
            )
        _audit(connection, "user.update", "success", actor_user_id, user_id)
        connection.commit()
        row = connection.execute(
            "SELECT id, username, role, active, created_at, updated_at "
            "FROM auth_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return {**dict(row), "active": bool(row["active"])}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_access_token(user_id: int, role: str, session_id: str) -> str:
    now = _now()
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            "sid": session_id,
            "iat": now,
            "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
            "jti": uuid.uuid4().hex,
        },
        _token_secret(),
        algorithm="HS256",
    )


def _token_response(
    user_id: int,
    username: str,
    role: str,
    session_id: str,
    refresh_token: str,
) -> dict[str, object]:
    return {
        "access_token": _issue_access_token(user_id, role, session_id),
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
        "user": {"id": user_id, "username": username, "role": role},
    }


def authenticate(
    db_path: Path,
    username: str,
    password: str,
) -> dict[str, object] | None:
    _token_secret()
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT id, username, password_hash, role, active "
            "FROM auth_users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()
        password_hash = str(row["password_hash"]) if row else _DUMMY_PASSWORD_HASH
        password_matches = verify_password(password_hash, password)
        if row is None or not row["active"] or not password_matches:
            _audit(
                connection,
                "auth.login",
                "failure",
                int(row["id"]) if row else None,
            )
            connection.commit()
            return None
        now = _now()
        session_id = str(uuid.uuid4())
        refresh_token = secrets.token_urlsafe(48)
        connection.execute(
            "INSERT INTO auth_sessions "
            "(id, user_id, refresh_token_hash, created_at, expires_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                row["id"],
                _token_hash(refresh_token),
                _timestamp(now),
                _timestamp(now + timedelta(days=REFRESH_TOKEN_DAYS)),
                _timestamp(now),
            ),
        )
        connection.execute(
            "INSERT INTO auth_refresh_tokens (token_hash, session_id) VALUES (?, ?)",
            (_token_hash(refresh_token), session_id),
        )
        _audit(connection, "auth.login", "success", int(row["id"]))
        connection.commit()
        return _token_response(
            int(row["id"]),
            str(row["username"]),
            str(row["role"]),
            session_id,
            refresh_token,
        )
    finally:
        connection.close()


def refresh_session(db_path: Path, refresh_token: str) -> dict[str, object] | None:
    _token_secret()
    now = _now()
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        token_hash = _token_hash(refresh_token)
        token_record = connection.execute(
            "SELECT token_hash, session_id, consumed_at "
            "FROM auth_refresh_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if token_record is not None and token_record["consumed_at"] is not None:
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? "
                "WHERE id = ? AND revoked_at IS NULL",
                (_timestamp(now), token_record["session_id"]),
            )
            _audit(connection, "auth.refresh.replay", "failure")
            connection.commit()
            return None
        row = connection.execute(
            "SELECT s.id AS session_id, s.user_id, u.username, u.role, u.active "
            "FROM auth_sessions s JOIN auth_users u ON u.id = s.user_id "
            "WHERE s.refresh_token_hash = ? AND s.revoked_at IS NULL "
            "AND s.expires_at > ?",
            (token_hash, _timestamp(now)),
        ).fetchone()
        if row is None or not row["active"]:
            _audit(connection, "auth.refresh", "failure")
            connection.commit()
            return None
        rotated_refresh = secrets.token_urlsafe(48)
        connection.execute(
            "UPDATE auth_refresh_tokens SET consumed_at = ? WHERE token_hash = ?",
            (_timestamp(now), token_hash),
        )
        connection.execute(
            "INSERT INTO auth_refresh_tokens (token_hash, session_id) VALUES (?, ?)",
            (_token_hash(rotated_refresh), row["session_id"]),
        )
        cursor = connection.execute(
            "UPDATE auth_sessions SET refresh_token_hash = ?, last_used_at = ? "
            "WHERE id = ? AND refresh_token_hash = ? AND revoked_at IS NULL",
            (
                _token_hash(rotated_refresh),
                _timestamp(now),
                row["session_id"],
                _token_hash(refresh_token),
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return None
        _audit(connection, "auth.refresh", "success", int(row["user_id"]))
        connection.commit()
        return _token_response(
            int(row["user_id"]),
            str(row["username"]),
            str(row["role"]),
            str(row["session_id"]),
            rotated_refresh,
        )
    finally:
        connection.close()


def authenticate_access_token(db_path: Path, token: str) -> dict[str, object] | None:
    try:
        payload = jwt.decode(
            token,
            _token_secret(),
            algorithms=["HS256"],
            options={"require": ["sub", "exp", "iat", "sid", "jti"]},
        )
        user_id = int(payload["sub"])
        session_id = str(payload["sid"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        return None
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT u.id, u.username, u.role, u.active "
            "FROM auth_sessions s JOIN auth_users u ON u.id = s.user_id "
            "WHERE s.id = ? AND s.user_id = ? AND s.revoked_at IS NULL "
            "AND s.expires_at > ?",
            (session_id, user_id, _timestamp(_now())),
        ).fetchone()
        if row is None or not row["active"]:
            return None
        return {
            "id": int(row["id"]),
            "username": str(row["username"]),
            "role": str(row["role"]),
            "session_id": session_id,
        }
    finally:
        connection.close()


def revoke_session(db_path: Path, session_id: str, user_id: int) -> bool:
    connection = connect(db_path)
    try:
        now = _timestamp(_now())
        cursor = connection.execute(
            "UPDATE auth_sessions SET revoked_at = ? "
            "WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (now, session_id, user_id),
        )
        _audit(
            connection,
            "auth.logout",
            "success" if cursor.rowcount else "failure",
            user_id,
        )
        connection.commit()
        return cursor.rowcount == 1
    finally:
        connection.close()


def _prompt_password() -> str:
    password = getpass.getpass("Password (12-128 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if not secrets.compare_digest(password, confirmation):
        raise ValueError("Passwords do not match")
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Market Intelligence API users")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin", help="Create the first administrator")
    bootstrap.add_argument("--username", required=True)
    create = commands.add_parser("create-user", help="Create a USER, READ_ONLY or ADMIN account")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=sorted(VALID_ROLES), default="USER")
    commands.add_parser("list-users", help="List accounts without credential data")
    args = parser.parse_args()

    initialize_database(DB_PATH)
    if args.command == "bootstrap-admin":
        user = bootstrap_admin(DB_PATH, args.username, _prompt_password())
    elif args.command == "create-user":
        user = create_user(DB_PATH, args.username, _prompt_password(), args.role)
    else:
        for user in list_users(DB_PATH):
            print(f"{user['id']}\t{user['username']}\t{user['role']}\tactive={user['active']}")
        return
    print(f"Created {user['role']} account {user['username']} (id={user['id']})")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, AccountConflictError) as exc:
        raise SystemExit(str(exc)) from exc
