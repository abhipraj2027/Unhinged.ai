import sqlite3, os, time
from contextlib import contextmanager
from prompts import ROAST_PROMPT, REWRITE_PROMPT

DB_PATH = os.getenv("DB_PATH", "./data/unhinged.db")
FREE_DAILY = 5
PRO_DAILY = 30

def _ensure_dir():
    d = os.path.dirname(DB_PATH)
    if d: os.makedirs(d, exist_ok=True)

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
        db.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            is_pro INTEGER DEFAULT 0,
            scans_used INTEGER DEFAULT 0,
            daily_scans INTEGER DEFAULT 0,
            daily_reset TEXT DEFAULT '',
            razorpay_sub_id TEXT,
            razorpay_pay_id TEXT,
            created_at REAL DEFAULT (strftime('%s','now')),
            expires_at REAL,
            last_scan_at REAL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS llm_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_key TEXT UNIQUE NOT NULL,
            config_value TEXT NOT NULL,
            updated_at REAL DEFAULT (strftime('%s','now')))""")
        for col, defn in [("daily_scans","INTEGER DEFAULT 0"),("daily_reset","TEXT DEFAULT ''")]:
            try: db.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
            except: pass
        rp = os.getenv("LLM_ROAST_PROVIDER","groq")
        rm = os.getenv("LLM_ROAST_MODEL","llama-3.3-70b-versatile")
        wp = os.getenv("LLM_REWRITE_PROVIDER","groq")
        wm = os.getenv("LLM_REWRITE_MODEL","llama-3.3-70b-versatile")
        defaults = {
            "roast_provider":rp, "roast_model":rm, "roast_max_tokens":"600", "roast_temperature":"1.0",
            "rewrite_provider":wp, "rewrite_model":wm, "rewrite_max_tokens":"800", "rewrite_temperature":"0.7",
            "roast_prompt":ROAST_PROMPT, "rewrite_prompt":REWRITE_PROMPT,
        }
        for k,v in defaults.items():
            db.execute("INSERT OR REPLACE INTO llm_config(config_key,config_value) VALUES(?,?)",(k,v))

def _today():
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d")

def get_or_create(email):
    email = email.strip().lower()
    with get_db() as db:
        r = db.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        if r: return dict(r)
        db.execute("INSERT INTO users(email) VALUES(?)",(email,))
        return dict(db.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone())

def get_user(email):
    email = email.strip().lower()
    with get_db() as db:
        r = db.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        return dict(r) if r else None

def reset_daily(email):
    email = email.strip().lower()
    today = _today()
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        if not u: return {}
        u = dict(u)
        if u.get("daily_reset") != today:
            db.execute("UPDATE users SET daily_scans=0,daily_reset=? WHERE email=?",(today,email))
            u["daily_scans"] = 0
        return u

def limit_for(user):
    return PRO_DAILY if user.get("is_pro") else FREE_DAILY

def remaining(user):
    return max(0, limit_for(user) - (user.get("daily_scans") or 0))

def inc_scan(email):
    email = email.strip().lower()
    today = _today()
    with get_db() as db:
        db.execute("""UPDATE users SET
            daily_scans=CASE WHEN daily_reset!=? THEN 1 ELSE daily_scans+1 END,
            daily_reset=?, scans_used=scans_used+1, last_scan_at=strftime('%s','now')
            WHERE email=?""",(today,today,email))

def set_pro(email, sub_id=None, pay_id=None):
    email = email.strip().lower()
    exp = time.time() + 30*86400
    with get_db() as db:
        u = db.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone()
        if u:
            db.execute("UPDATE users SET is_pro=1,expires_at=?,razorpay_sub_id=COALESCE(?,razorpay_sub_id),razorpay_pay_id=COALESCE(?,razorpay_pay_id) WHERE email=?",(exp,sub_id,pay_id,email))
        else:
            db.execute("INSERT INTO users(email,is_pro,expires_at,razorpay_sub_id,razorpay_pay_id) VALUES(?,1,?,?,?)",(email,exp,sub_id,pay_id))

def unset_pro(email):
    with get_db() as db:
        db.execute("UPDATE users SET is_pro=0 WHERE email=?",(email.strip().lower(),))

_cfg_cache = {}
_cfg_ts = 0

def get_config():
    global _cfg_cache, _cfg_ts
    if _cfg_cache and (time.time()-_cfg_ts)<60: return _cfg_cache
    with get_db() as db:
        rows = db.execute("SELECT config_key,config_value FROM llm_config").fetchall()
        _cfg_cache = {r["config_key"]:r["config_value"] for r in rows}
        _cfg_ts = time.time()
    return _cfg_cache

def set_config(key, value):
    global _cfg_ts
    with get_db() as db:
        db.execute("INSERT INTO llm_config(config_key,config_value,updated_at) VALUES(?,?,strftime('%s','now')) ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value,updated_at=excluded.updated_at",(key,value))
    _cfg_ts = 0

def get_stats():
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        pro = db.execute("SELECT COUNT(*) c FROM users WHERE is_pro=1").fetchone()["c"]
        scans = db.execute("SELECT COALESCE(SUM(scans_used),0) c FROM users").fetchone()["c"]
        today = db.execute("SELECT COUNT(*) c FROM users WHERE last_scan_at>?",(time.time()-86400,)).fetchone()["c"]
        recent = db.execute("SELECT email,scans_used,last_scan_at,is_pro FROM users WHERE last_scan_at IS NOT NULL ORDER BY last_scan_at DESC LIMIT 10").fetchall()
        return {"total_users":total,"pro_users":pro,"total_scans":scans,"active_today":today,"recent":[dict(r) for r in recent]}
