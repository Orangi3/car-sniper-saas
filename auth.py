"""
auth.py — accounts, sessions, entitlement decorators.

Single source of truth for "who is this request from, and what may they do?"
Replaces the legacy owner-by-IP + shared-password gate.

Design choices:
  * passwords      → bcrypt (12 rounds), never stored or logged in plaintext
  * sessions       → opaque random token (32B from secrets); the cookie
                     carries the token, the `sessions` table only its
                     SHA-256 (HttpOnly, Secure when behind HTTPS,
                     SameSite=Lax). 30-day TTL; revoke_sessions() signs a
                     user out everywhere.
  * server-side    → every decision uses values read fresh from the DB on the
                     current request. Client cookies / JSON / query strings
                     NEVER carry user_id, role, or plan — those are looked up.
  * decorators     → @login_required, @plan_required(min_plan),
                     @admin_required (alias for role == 'admin').
  * Flask wiring   → g.user holds the authenticated User; current_user()
                     returns it or None.

Rate-limited login: 10 failed attempts per visitor address per 5 minutes
triggers 429, counted in the database (shared by all web workers). Behind
Caddy the address comes from X-Forwarded-For, trusted only from
SNIPER_TRUSTED_PROXIES (client_ip()).
Registration is open by default; flip ALLOW_REGISTRATION env to "0" to lock it.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import secrets
import time
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

# Cookies set Secure when the request arrived via HTTPS, or the configured
# public origin is https (deployed behind Caddy, even if the proxy's scheme
# header were lost), but not on the dev server over http://127.0.0.1.
# SameSite=Lax keeps cookies on top-level navigations while blocking the
# common CSRF flows.
def _cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "samesite": "Lax",
        "secure": request.is_secure
                  or (os.environ.get("BILLING_ORIGIN") or "").startswith("https://"),
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


_DUMMY_HASH: Optional[str] = None


def password_matches(plain: str, stored_hash: Optional[str]) -> bool:
    """verify_password for sign-in. An unknown account (no stored hash)
    still costs one bcrypt check, so the response time doesn't reveal which
    email addresses have accounts."""
    global _DUMMY_HASH
    if not stored_hash:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = bcrypt.hashpw(secrets.token_hex(16).encode(),
                                        bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")
        verify_password(plain, _DUMMY_HASH)
        return False
    return verify_password(plain, stored_hash)


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
                plan: str = "free", invite: Optional[str] = None,
                require_invite: bool = False) -> User:
    """`require_invite`: the account is created only together with claiming
    `invite` (invites.claim) in the same transaction — a failed claim
    creates no account, a failed insert leaves the invitation unused. A
    token that can't be claimed is refused before the password is hashed,
    so made-up tokens can't be used to make the server burn CPU on bcrypt."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("invalid email")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}")
    if plan not in PLAN_TIERS:
        raise ValueError(f"plan must be one of {PLAN_TIERS}")
    if require_invite:
        import invites
        if not invites.is_claimable(invite):
            raise invites.InviteError()
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
        if require_invite:
            invites.claim(conn, invite, int(uid))   # raises InviteError → rollback
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


def _token_hash(token: str) -> str:
    """What sessions.token stores: the SHA-256 of the cookie value (migration
    0018), so a copy of the database can't be replayed as a session. The
    token is 256 random bits, so a plain hash is enough (no salt or slow
    hash), and the lookup is an indexed equality on the hash."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user: User) -> str:
    """New random session for `user`; returns the cookie value. Expired
    sessions are deleted here, so the table doesn't grow without bound."""
    token = _new_token()
    now = db.now_utc_iso()
    expires = (datetime.now(timezone.utc)
               + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    ua = (request.headers.get("User-Agent") or "")[:300]
    ip = client_ip()[:64]
    with db.transaction() as conn:
        _exec(conn, f"DELETE FROM sessions WHERE expires_at <= {_PH()}", (now,))
        _exec(conn,
              f"INSERT INTO sessions(token, user_id, expires_at, user_agent, ip) "
              f"VALUES ({_PH()},{_PH()},{_PH()},{_PH()},{_PH()})",
              (_token_hash(token), user.id, expires, ua, ip))
        _exec(conn,
              f"UPDATE users SET last_login_at={_PH()} WHERE id={_PH()}",
              (now, user.id))
    return token


def destroy_session(token: str) -> None:
    if not token:
        return
    with db.transaction() as conn:
        _exec(conn, f"DELETE FROM sessions WHERE token={_PH()}", (_token_hash(token),))


def revoke_sessions(user_id: int) -> int:
    """Sign a user out everywhere (lost device, suspected compromise):
        python -c "import auth; print(auth.revoke_sessions(auth.get_user_by_email('pat@example.com').id))"
    Returns how many sessions were removed."""
    with db.transaction() as conn:
        return _exec(conn, f"DELETE FROM sessions WHERE user_id={_PH()}",
                     (int(user_id),)).rowcount


def _user_for_token(token: str) -> Optional[User]:
    if not token or len(token) > 256:
        return None
    now = db.now_utc_iso()
    with db.transaction() as conn:
        cur = _exec(conn,
            f"SELECT u.id, u.email, u.role, u.plan "
            f"FROM sessions s JOIN users u ON u.id = s.user_id "
            f"WHERE s.token={_PH()} AND s.expires_at > {_PH()}",
            (_token_hash(token), now))
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


# ---------- Client address (behind the Caddy reverse proxy) -----------
#
# Behind Caddy every connection reaches gunicorn from loopback, so the
# socket address is the proxy, not the visitor. The visitor's address is
# read from X-Forwarded-For, but only when the connecting peer is listed in
# SNIPER_TRUSTED_PROXIES (IPs or CIDRs; deploy sets 127.0.0.1,::1). The
# header is walked from the right, skipping trusted hops, so a value a
# client wrote itself can't be used to pose as someone else. With nothing
# listed, the header is ignored and the socket address is used.

_log = logging.getLogger("sniper.auth")


def _parse_proxies(spec: str) -> tuple:
    nets = []
    for part in re.split(r"[,\s]+", spec or ""):
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            _log.warning("SNIPER_TRUSTED_PROXIES: ignoring %r (not an IP or CIDR)", part)
    return tuple(nets)


TRUSTED_PROXIES = _parse_proxies(os.environ.get("SNIPER_TRUSTED_PROXIES", ""))
_misconfig_warned = False


def _ip(value):
    """An IP address from a socket address or one X-Forwarded-For hop
    ('1.2.3.4', '1.2.3.4:5678', '2001:db8::1', '[2001:db8::1]:443'), or None."""
    v = (value or "").strip()
    if v.startswith("["):
        v = v[1:].split("]", 1)[0]
    elif v.count(":") == 1:
        v = v.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(v)
    except ValueError:
        return None
    return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip


def _trusted(ip) -> bool:
    return any(ip in net for net in TRUSTED_PROXIES)


def client_ip() -> str:
    """The address of the person making the current request."""
    global _misconfig_warned
    peer = _ip(request.remote_addr)
    if peer is None:
        return request.remote_addr or "unknown"
    hops = [h for h in ",".join(request.headers.getlist("X-Forwarded-For")).split(",") if h.strip()]
    if not _trusted(peer):
        if hops and peer.is_loopback and not TRUSTED_PROXIES and not _misconfig_warned:
            _misconfig_warned = True
            _log.warning("requests arrive through a local proxy but SNIPER_TRUSTED_PROXIES "
                         "is not set: every visitor looks like %s, so they share one "
                         "login-attempt budget", peer)
        return str(peer)
    for hop in reversed(hops):
        ip = _ip(hop)
        if ip is None:
            return str(peer)        # a hop we can't read: trust nothing further left
        if not _trusted(ip):
            return str(ip)
    return str(peer)


def proxy_misconfigured() -> bool:
    """True when this request came through a local proxy whose forwarded
    address is being ignored (SNIPER_TRUSTED_PROXIES unset or wrong)."""
    peer = _ip(request.remote_addr)
    return bool(peer is not None and peer.is_loopback and not _trusted(peer)
                and request.headers.get("X-Forwarded-For"))


def rate_key(ip: str) -> str:
    """Throttle bucket for an address. An IPv6 visitor normally controls a
    whole /64, so addresses in one /64 share a bucket."""
    a = _ip(ip)
    if a is not None and a.version == 6:
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return ip


# ---------- Login throttle (per visitor, shared by every web worker) ----
# In the database (migration 0017) so all gunicorn workers count against
# one budget and a restart doesn't reset it.

LOGIN_WINDOW_S = 300        # 5 minutes
LOGIN_MAX_FAILS = 10


def login_throttled() -> bool:
    """Returns True if the current visitor is over the failure budget. Call
    this BEFORE checking the password so we don't reveal hit/miss timing."""
    with db.transaction() as conn:
        row = _exec(conn, f"SELECT COUNT(*) AS n FROM login_failures "
                          f"WHERE rate_key = {_PH()} AND at > {_PH()}",
                    (rate_key(client_ip()), time.time() - LOGIN_WINDOW_S)).fetchone()
    return int(row["n"]) >= LOGIN_MAX_FAILS


def record_login_failure() -> None:
    now = time.time()
    with db.transaction() as conn:
        _exec(conn, f"INSERT INTO login_failures(rate_key, at) VALUES ({_PH()},{_PH()})",
              (rate_key(client_ip()), now))
        _exec(conn, f"DELETE FROM login_failures WHERE at < {_PH()}", (now - LOGIN_WINDOW_S,))


def clear_login_failures() -> None:
    with db.transaction() as conn:
        _exec(conn, f"DELETE FROM login_failures WHERE rate_key = {_PH()}",
              (rate_key(client_ip()),))
