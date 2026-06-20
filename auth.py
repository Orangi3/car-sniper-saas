"""
auth.py — accounts, sessions, entitlement decorators.

Single source of truth for "who is this request from, and what may they do?"
Replaces the legacy owner-by-IP + shared-password gate.

Design choices:
  * passwords      → bcrypt (12 rounds), never stored or logged in plaintext
  * sessions       → opaque random token (32B from secrets) stored in `sessions`
                     table; only the token rides in the cookie (HttpOnly,
                     Secure when behind HTTPS, SameSite=Lax). 30-day TTL.
  * server-side    → every decision uses values read fresh from the DB on the
                     current request. Client cookies / JSON / query strings
                     NEVER carry user_id, role, or plan — those are looked up.
  * decorators     → @login_required, @plan_required(min_plan),
                     @admin_required (alias for role == 'admin').
  * Flask wiring   → g.user holds the authenticated User; current_user()
                     returns it or None.

Rate-limited login: 10 failed attempts per IP per 5 minutes triggers 429.
Registration is open by default; flip ALLOW_REGISTRATION env to "0" to lock it.
"""
from __future__ import annotations

import os
import re
import secrets
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable, Optional

import bcrypt
from flask import g, jsonify, request

import db

# ---------- Plans / roles ---------------------------------------------

# Tier ordering for plan_required(). 'admin' is a *role*, not a plan, but
# admins implicitly clear every plan gate so internal/staff accounts work.
PLAN_TIERS = ("free", "starter", "pro")
VALID_ROLES = ("user", "admin")

SESSION_COOKIE = "sniper_session"
SESSION_TTL_DAYS = 30

# Open registration unless explicitly disabled. Set ALLOW_REGISTRATION=0 to
# lock down to admin-created users only.
ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "1") != "0"

# Cookies set Secure when the request arrived via HTTPS (real prod) but not
# when reaching the dev server over http://127.0.0.1. SameSite=Lax keeps
# cookies on top-level navigations while blocking the common CSRF flows.
def _cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "samesite": "Lax",
        "secure": request.is_secure,
        "max_age": SESSION_TTL_DAYS * 86400,
        "path": "/",
    }


# ---------- User / session models -------------------------------------

@dataclass
class User:
    id: int
    email: str
    role: str
    plan: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def has_plan(self, min_plan: str) -> bool:
        if self.is_admin:
            return True
        try:
            return PLAN_TIERS.index(self.plan) >= PLAN_TIERS.index(min_plan)
        except ValueError:
            return False

    def public_dict(self) -> dict:
        # Never include the password hash, never include internal fields.
        return {"id": self.id, "email": self.email,
                "role": self.role, "plan": self.plan,
                "is_admin": self.is_admin}


# ---------- Password hashing ------------------------------------------

# bcrypt rounds. 12 is the modern default — ~250ms per check on a laptop,
# fast enough for a login form, slow enough to choke brute-force.
BCRYPT_ROUNDS = 12

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def hash_password(plain: str) -> str:
    if not isinstance(plain, str) or len(plain) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(plain) > 200:
        raise ValueError("password too long")
    return bcrypt.hashpw(plain.encode("utf-8"),
                         bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"),
                              stored_hash.encode("ascii"))
    except (ValueError, AttributeError):
        return False


# ---------- DB access (raw SQL — keeps the layer thin) ----------------

_PH = db.placeholder  # "?" for SQLite, "%s" for Postgres


def _row_to_user(row) -> User:
    # Both sqlite3.Row and psycopg2 DictRow support item access by name.
    return User(id=int(row["id"]), email=str(row["email"]),
                role=str(row["role"]), plan=str(row["plan"]))


def _exec(conn, sql: str, params: tuple = ()):
    """Uniform exec wrapper — sqlite Connection.execute vs psycopg2 cursor()."""
    if db.IS_PG:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur
    return conn.execute(sql, params)


def get_user_by_email(email: str) -> Optional[User]:
    email = (email or "").strip().lower()
    if not email:
        return None
    with db.transaction() as conn:
        cur = _exec(conn,
                    f"SELECT id, email, role, plan FROM users WHERE LOWER(email)=LOWER({_PH()})",
                    (email,))
        row = cur.fetchone()
        return _row_to_user(row) if row else None


def get_user_by_id(uid: int) -> Optional[User]:
    if not uid:
        return None
    with db.transaction() as conn:
        cur = _exec(conn,
                    f"SELECT id, email, role, plan FROM users WHERE id={_PH()}",
                    (int(uid),))
        row = cur.fetchone()
        return _row_to_user(row) if row else None


def create_user(email: str, password: str, *, role: str = "user",
                plan: str = "free") -> User:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("invalid email")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}")
    if plan not in PLAN_TIERS:
        raise ValueError(f"plan must be one of {PLAN_TIERS}")
    pw_hash = hash_password(password)
    now = db.now_utc_iso()
    with db.transaction() as conn:
        try:
            if db.IS_PG:
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO users(email, password_hash, role, plan, created_at) "
                    f"VALUES ({_PH()},{_PH()},{_PH()},{_PH()},{_PH()}) RETURNING id",
                    (email, pw_hash, role, plan, now))
                uid = cur.fetchone()["id"]
            else:
                cur = conn.execute(
                    f"INSERT INTO users(email, password_hash, role, plan, created_at) "
                    f"VALUES (?,?,?,?,?)",
                    (email, pw_hash, role, plan, now))
                uid = cur.lastrowid
        except Exception as e:
            # Unique constraint on email is the only expected failure here.
            raise ValueError("email already registered") from e
    return User(id=int(uid), email=email, role=role, plan=plan)


def set_plan(user_id: int, plan: str) -> None:
    if plan not in PLAN_TIERS:
        raise ValueError(f"plan must be one of {PLAN_TIERS}")
    with db.transaction() as conn:
        _exec(conn, f"UPDATE users SET plan={_PH()} WHERE id={_PH()}",
              (plan, int(user_id)))


def set_role(user_id: int, role: str) -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}")
    with db.transaction() as conn:
        _exec(conn, f"UPDATE users SET role={_PH()} WHERE id={_PH()}",
              (role, int(user_id)))


# ---------- Sessions ---------------------------------------------------

def _new_token() -> str:
    # 32 bytes ≈ 256 bits — collision-proof, URL-safe.
    return secrets.token_urlsafe(32)


def create_session(user: User) -> str:
    token = _new_token()
    expires = (datetime.now(timezone.utc)
               + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    ua = (request.headers.get("User-Agent") or "")[:300]
    ip = (request.headers.get("X-Forwarded-For") or
          request.remote_addr or "")[:64]
    with db.transaction() as conn:
        _exec(conn,
              f"INSERT INTO sessions(token, user_id, expires_at, user_agent, ip) "
              f"VALUES ({_PH()},{_PH()},{_PH()},{_PH()},{_PH()})",
              (token, user.id, expires, ua, ip))
        _exec(conn,
              f"UPDATE users SET last_login_at={_PH()} WHERE id={_PH()}",
              (db.now_utc_iso(), user.id))
    return token


def destroy_session(token: str) -> None:
    if not token:
        return
    with db.transaction() as conn:
        _exec(conn, f"DELETE FROM sessions WHERE token={_PH()}", (token,))


def _user_for_token(token: str) -> Optional[User]:
    if not token:
        return None
    now = db.now_utc_iso()
    with db.transaction() as conn:
        cur = _exec(conn,
            f"SELECT u.id, u.email, u.role, u.plan "
            f"FROM sessions s JOIN users u ON u.id = s.user_id "
            f"WHERE s.token={_PH()} AND s.expires_at > {_PH()}",
            (token, now))
        row = cur.fetchone()
        return _row_to_user(row) if row else None


# ---------- Request context ------------------------------------------

def load_user_from_request() -> Optional[User]:
    """Called at the start of every request (server.py before_request).
    Stashes the resolved user on flask.g so handlers/decorators don't have
    to re-query."""
    token = request.cookies.get(SESSION_COOKIE)
    user = _user_for_token(token) if token else None
    g.user = user
    return user


def current_user() -> Optional[User]:
    return getattr(g, "user", None)


# ---------- Decorators ------------------------------------------------

def login_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return jsonify({"error": "authentication required"}), 401
        return fn(*a, **kw)
    return wrapper


def plan_required(min_plan: str) -> Callable:
    if min_plan not in PLAN_TIERS:
        raise ValueError(f"min_plan must be one of {PLAN_TIERS}")

    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if not u:
                return jsonify({"error": "authentication required"}), 401
            if not u.has_plan(min_plan):
                return jsonify({"error": "upgrade required",
                                "current_plan": u.plan,
                                "required_plan": min_plan}), 402
            return fn(*a, **kw)
        return wrapper
    return deco


def admin_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return jsonify({"error": "authentication required"}), 401
        if not u.is_admin:
            return jsonify({"error": "admin only"}), 403
        return fn(*a, **kw)
    return wrapper


# ---------- Login throttle (in-memory, per-IP) ------------------------

_LOGIN_FAILS: dict[str, list[float]] = {}
_LOGIN_LOCK = threading.Lock()
LOGIN_WINDOW_S = 300        # 5 minutes
LOGIN_MAX_FAILS = 10


def login_throttled() -> bool:
    """Returns True if the current IP is over the failure budget. Call this
    BEFORE checking the password so we don't reveal hit/miss timing."""
    ip = request.remote_addr or "unknown"
    now = time.time()
    with _LOGIN_LOCK:
        bucket = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < LOGIN_WINDOW_S]
        _LOGIN_FAILS[ip] = bucket
        return len(bucket) >= LOGIN_MAX_FAILS


def record_login_failure() -> None:
    ip = request.remote_addr or "unknown"
    with _LOGIN_LOCK:
        _LOGIN_FAILS.setdefault(ip, []).append(time.time())


def clear_login_failures() -> None:
    ip = request.remote_addr or "unknown"
    with _LOGIN_LOCK:
        _LOGIN_FAILS.pop(ip, None)
