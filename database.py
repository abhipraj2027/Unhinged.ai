import sqlite3
import os
import time
from contextlib import contextmanager
from prompts import ROAST_PROMPT, REWRITE_PROMPT

DB_PATH = os.getenv("DB_PATH", "/app/data/unhinged.db")
FREE_DAILY_LIMIT = 5
PRO_DAILY_LIMIT = 30
FREE_LIMIT = FREE_DAILY_LIMIT  # backward compat


def _ensure_dir():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)


@contextmanager
def get_db():
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                is_pro INTEGER DEFAULT 0,
                scans_used INTEGER DEFAULT 0,
                daily_scans INTEGER DEFAULT 0,
                daily_reset_date TEXT DEFAULT '',
                razorpay_subscription_id TEXT,
                razorpay_payment_id TEXT,
                created_at REAL DEFAULT (strftime('%s','now')),
                expires_at REAL,
                last_scan_at REAL
            )
        """)
        # Add columns if upgrading existing DB
        for col, defn in [
            ("daily_scans", "INTEGER DEFAULT 0"),
            ("daily_reset_date", "TEXT DEFAULT ''"),
        ]:
            try:
                db.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
            except Exception:
                pass

        db.execute("""
            CREATE TABLE IF NOT EXISTS llm_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_key TEXT UNIQUE NOT NULL,
                config_value TEXT NOT NULL,
                updated_at REAL DEFAULT (strftime('%s','now'))
            )
        """)

        # Always force Google as default (override any stale anthropic/openai entries)
        roast_provider  = os.getenv("LLM_ROAST_PROVIDER",  "groq")
        roast_model     = os.getenv("LLM_ROAST_MODEL",     "llama-3.3-70b-versatile")
        rewrite_provider= os.getenv("LLM_REWRITE_PROVIDER","groq")
        rewrite_model   = os.getenv("LLM_REWRITE_MODEL",   "llama-3.3-70b-versatile")

        defaults = {
            "roast_provider":    roast_provider,
            "roast_model":       roast_model,
            "roast_max_tokens":  "600",
            "roast_temperature": "1.0",
            "rewrite_provider":  rewrite_provider,
            "rewrite_model":     rewrite_model,
            "rewrite_max_tokens":"800",
            "rewrite_temperature":"0.7",
            "roast_prompt":      ROAST_PROMPT,
            "rewrite_prompt":    REWRITE_PROMPT,
        }
        for k, v in defaults.items():
            db.execute(
                "INSERT OR REPLACE INTO llm_config (config_key, config_value) VALUES (?, ?)",
                (k, v),
            )


# ── User helpers ──────────────────────────────────────────────

def get_or_create_user(email: str) -> dict:
    email = email.strip().lower()
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return dict(row)
        db.execute("INSERT INTO users (email) VALUES (?)", (email,))
        return dict(db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())


def get_user(email: str):
    email = email.strip().lower()
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def _get_today() -> str:
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d")


def reset_daily_if_needed(email: str) -> dict:
    today = _get_today()
    email = email.strip().lower()
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            return {}
        user = dict(user)
        if user.get("daily_reset_date") != today:
            db.execute(
                "UPDATE users SET daily_scans=0, daily_reset_date=? WHERE email=?",
                (today, email)
            )
            user["daily_scans"] = 0
            user["daily_reset_date"] = today
    return user

# alias used in main.py
_reset_daily_if_needed = reset_daily_if_needed


def daily_limit(user: dict) -> int:
    return PRO_DAILY_LIMIT if user.get("is_pro") else FREE_DAILY_LIMIT


def scans_remaining(user: dict) -> int:
    daily = user.get("daily_scans") or 0
    return max(0, daily_limit(user) - daily)


def increment_scan(email: str):
    email = email.strip().lower()
    today = _get_today()
    with get_db() as db:
        db.execute(
            """UPDATE users SET
               daily_scans = CASE WHEN daily_reset_date != ? THEN 1 ELSE daily_scans + 1 END,
               daily_reset_date = ?,
               scans_used = scans_used + 1,
               last_scan_at = strftime('%s','now')
               WHERE email = ?""",
            (today, today, email),
        )


def set_pro(email: str, subscription_id: str = None, payment_id: str = None):
    email = email.strip().lower()
    expires = time.time() + 30 * 86400
    with get_db() as db:
        user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if user:
            db.execute(
                """UPDATE users SET is_pro=1, expires_at=?,
                   razorpay_subscription_id=COALESCE(?,razorpay_subscription_id),
                   razorpay_payment_id=COALESCE(?,razorpay_payment_id)
                   WHERE email=?""",
                (expires, subscription_id, payment_id, email),
            )
        else:
            db.execute(
                """INSERT INTO users (email, is_pro, expires_at, razorpay_subscription_id, razorpay_payment_id)
                   VALUES (?,1,?,?,?)""",
                (email, expires, subscription_id, payment_id),
            )


def unset_pro(email: str):
    email = email.strip().lower()
    with get_db() as db:
        db.execute("UPDATE users SET is_pro=0 WHERE email=?", (email,))


# ── Config helpers ────────────────────────────────────────────

_config_cache = {}
_cache_ts = 0
CACHE_TTL = 60


def get_all_config() -> dict:
    global _config_cache, _cache_ts
    now = time.time()
    if _config_cache and (now - _cache_ts) < CACHE_TTL:
        return _config_cache
    with get_db() as db:
        rows = db.execute("SELECT config_key, config_value FROM llm_config").fetchall()
        _config_cache = {r["config_key"]: r["config_value"] for r in rows}
        _cache_ts = now
    return _config_cache


def set_config(key: str, value: str):
    global _cache_ts
    with get_db() as db:
        db.execute(
            """INSERT INTO llm_config (config_key, config_value, updated_at)
               VALUES (?, ?, strftime('%s','now'))
               ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value,
               updated_at=excluded.updated_at""",
            (key, value),
        )
    _cache_ts = 0


# ── Stats ─────────────────────────────────────────────────────

def get_stats() -> dict:
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        pro   = db.execute("SELECT COUNT(*) c FROM users WHERE is_pro=1").fetchone()["c"]
        total_scans = db.execute("SELECT COALESCE(SUM(scans_used),0) c FROM users").fetchone()["c"]
        now = time.time()
        today = db.execute(
            "SELECT COUNT(*) c FROM users WHERE last_scan_at > ?", (now - 86400,)
        ).fetchone()["c"]
        recent = db.execute(
            "SELECT email, scans_used, last_scan_at, is_pro FROM users WHERE last_scan_at IS NOT NULL ORDER BY last_scan_at DESC LIMIT 10"
        ).fetchall()
        return {
            "total_users": total,
            "pro_users": pro,
            "total_scans": total_scans,
            "active_today": today,
            "recent": [dict(r) for r in recent],
        }
