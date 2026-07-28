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
        # Teams & auth tables
        db.execute("""CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, owner_email TEXT NOT NULL,
            seats INTEGER DEFAULT 5, is_active INTEGER DEFAULT 1, invite_code TEXT UNIQUE,
            razorpay_sub_id TEXT, created_at REAL DEFAULT (strftime('%s','now')))""")
        db.execute("""CREATE TABLE IF NOT EXISTS team_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INTEGER NOT NULL, email TEXT NOT NULL,
            role TEXT DEFAULT 'member', joined_at REAL DEFAULT (strftime('%s','now')),
            UNIQUE(team_id, email), FOREIGN KEY(team_id) REFERENCES teams(id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS scan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, score REAL,
            scanned_at REAL DEFAULT (strftime('%s','now')))""")
        # Add columns if upgrading
        for col, defn in [("daily_scans","INTEGER DEFAULT 0"),("daily_reset","TEXT DEFAULT ''"),("password_hash","TEXT")]:
            try: db.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
            except: pass
        try: db.execute("ALTER TABLE teams ADD COLUMN invite_code TEXT")
        except: pass

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

# ── Auth helpers (bcrypt + JWT) ────────────────────────────────

from passlib.hash import bcrypt as _bcrypt

def hash_password(pw):
    return _bcrypt.hash(pw)

def verify_password(pw, hashed):
    try:
        return _bcrypt.verify(pw, hashed)
    except Exception:
        return False

def set_password(email, password):
    email = email.strip().lower()
    h = hash_password(password)
    get_or_create(email)
    with get_db() as db:
        db.execute("UPDATE users SET password_hash=? WHERE email=?", (h, email))

def check_password(email, password):
    email = email.strip().lower()
    with get_db() as db:
        r = db.execute("SELECT password_hash FROM users WHERE email=?", (email,)).fetchone()
        if not r or not r["password_hash"]:
            return False
        return verify_password(password, r["password_hash"])

def has_password(email):
    email = email.strip().lower()
    with get_db() as db:
        r = db.execute("SELECT password_hash FROM users WHERE email=?", (email,)).fetchone()
        return bool(r and r["password_hash"])

def create_reset_token(email):
    import random, string
    email = email.strip().lower()
    user = get_user(email)
    if not user: return None
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    expires = time.time() + 3600  # 1 hour
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at REAL NOT NULL,
            used INTEGER DEFAULT 0)""")
        db.execute("INSERT INTO reset_tokens(email,token,expires_at) VALUES(?,?,?)", (email, token, expires))
    return token

def verify_reset_token(token):
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at REAL NOT NULL,
            used INTEGER DEFAULT 0)""")
        row = db.execute("SELECT * FROM reset_tokens WHERE token=? AND used=0", (token,)).fetchone()
        if not row: return None
        if time.time() > row["expires_at"]: return None
        return row["email"]

def use_reset_token(token):
    with get_db() as db:
        db.execute("UPDATE reset_tokens SET used=1 WHERE token=?", (token,))

def get_user_profile(email):
    email = email.strip().lower()
    user = get_user(email)
    if not user: return None
    user = reset_daily(email) or user
    team = get_team_for_email(email)
    return {
        "email": email,
        "is_pro": bool(user.get("is_pro")),
        "scans_used": user.get("scans_used", 0),
        "daily_scans": user.get("daily_scans", 0),
        "scans_remaining": remaining(user),
        "daily_limit": limit_for(user),
        "team": dict(team) if team else None,
        "expires_at": user.get("expires_at"),
        "created_at": user.get("created_at"),
    }

# ── Team helpers ──────────────────────────────────────────────

def _gen_invite_code():
    import random, string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def create_team(name, owner_email, seats=5):
    owner_email = owner_email.strip().lower()
    code = _gen_invite_code()
    with get_db() as db:
        db.execute("INSERT INTO teams(name,owner_email,seats,invite_code) VALUES(?,?,?,?)", (name, owner_email, seats, code))
        team = db.execute("SELECT * FROM teams WHERE owner_email=? ORDER BY id DESC LIMIT 1", (owner_email,)).fetchone()
        team_id = team["id"]
        db.execute("INSERT OR IGNORE INTO team_members(team_id,email,role) VALUES(?,?,?)", (team_id, owner_email, "owner"))
        # Create user if not exists (inline, no nested connection)
        existing = db.execute("SELECT id FROM users WHERE email=?", (owner_email,)).fetchone()
        if not existing:
            db.execute("INSERT INTO users(email) VALUES(?)", (owner_email,))
        db.execute("UPDATE users SET is_pro=1, expires_at=? WHERE email=?", (time.time()+365*86400, owner_email))
        return dict(team)

def add_team_member(team_id, email):
    email = email.strip().lower()
    with get_db() as db:
        team = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team: return {"error": "Team not found"}
        current = db.execute("SELECT COUNT(*) c FROM team_members WHERE team_id=?", (team_id,)).fetchone()["c"]
        if current >= team["seats"]: return {"error": f"Team full ({team['seats']} seats)"}
        db.execute("INSERT OR IGNORE INTO team_members(team_id,email,role) VALUES(?,?,?)", (team_id, email, "member"))
        existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if not existing:
            db.execute("INSERT INTO users(email) VALUES(?)", (email,))
        db.execute("UPDATE users SET is_pro=1, expires_at=? WHERE email=?", (time.time()+365*86400, email))
        return {"success": True, "email": email}

def remove_team_member(team_id, email):
    email = email.strip().lower()
    with get_db() as db:
        db.execute("DELETE FROM team_members WHERE team_id=? AND email=? AND role!='owner'", (team_id, email))
        db.execute("UPDATE users SET is_pro=0 WHERE email=?", (email,))
        return {"success": True}

def get_team_for_email(email):
    email = email.strip().lower()
    with get_db() as db:
        row = db.execute("""SELECT t.* FROM teams t
            JOIN team_members tm ON t.id=tm.team_id
            WHERE tm.email=? AND t.is_active=1 LIMIT 1""", (email,)).fetchone()
        return dict(row) if row else None

def get_team_members(team_id):
    with get_db() as db:
        rows = db.execute("""SELECT tm.email, tm.role, tm.joined_at,
            COALESCE(u.scans_used,0) as scans_used,
            COALESCE(u.daily_scans,0) as daily_scans
            FROM team_members tm
            LEFT JOIN users u ON tm.email=u.email
            WHERE tm.team_id=?
            ORDER BY tm.role DESC, u.scans_used DESC""", (team_id,)).fetchall()
        return [dict(r) for r in rows]

def get_team_by_owner(owner_email):
    owner_email = owner_email.strip().lower()
    with get_db() as db:
        row = db.execute("SELECT * FROM teams WHERE owner_email=? AND is_active=1 ORDER BY id DESC LIMIT 1", (owner_email,)).fetchone()
        return dict(row) if row else None

def get_all_teams():
    with get_db() as db:
        rows = db.execute("""SELECT t.*,
            (SELECT COUNT(*) FROM team_members WHERE team_id=t.id) as member_count
            FROM teams t WHERE t.is_active=1 ORDER BY t.created_at DESC""").fetchall()
        return [dict(r) for r in rows]

def join_team_by_code(email, invite_code):
    email = email.strip().lower()
    with get_db() as db:
        team = db.execute("SELECT * FROM teams WHERE invite_code=? AND is_active=1", (invite_code,)).fetchone()
        if not team: return {"error": "Invalid invite code"}
        team = dict(team)
        current = db.execute("SELECT COUNT(*) c FROM team_members WHERE team_id=?", (team["id"],)).fetchone()["c"]
        if current >= team["seats"]: return {"error": f"Team full ({team['seats']} seats)"}
        existing = db.execute("SELECT id FROM team_members WHERE team_id=? AND email=?", (team["id"], email)).fetchone()
        if existing: return {"error": "Already on this team"}
        db.execute("INSERT INTO team_members(team_id,email,role) VALUES(?,?,?)", (team["id"], email, "member"))
        existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if not existing:
            db.execute("INSERT INTO users(email) VALUES(?)", (email,))
        db.execute("UPDATE users SET is_pro=1, expires_at=? WHERE email=?", (time.time()+365*86400, email))
        return {"success": True, "team_name": team["name"]}

def log_scan(email, score):
    email = email.strip().lower()
    with get_db() as db:
        db.execute("INSERT INTO scan_log(email,score) VALUES(?,?)", (email, score))

def get_leaderboard(team_id, days=7):
    """Get basic per-user stats for the team."""
    cutoff = time.time() - days * 86400
    with get_db() as db:
        rows = db.execute("""SELECT sl.email,
            COUNT(*) as total_scans,
            ROUND(AVG(sl.score),1) as avg_score,
            ROUND(MAX(sl.score),1) as max_score,
            ROUND(MIN(sl.score),1) as min_score
            FROM scan_log sl
            JOIN team_members tm ON sl.email=tm.email AND tm.team_id=?
            WHERE sl.scanned_at > ?
            GROUP BY sl.email
            ORDER BY total_scans DESC""", (team_id, cutoff)).fetchall()
        return [dict(r) for r in rows]

def get_team_awards(team_id):
    """Generate weekly awards for the team."""
    now = time.time()
    this_week = now - 7 * 86400
    last_week_start = now - 14 * 86400

    with get_db() as db:
        # This week stats per user
        this_week_stats = db.execute("""SELECT sl.email,
            COUNT(*) as scans,
            ROUND(AVG(sl.score),1) as avg,
            ROUND(MAX(sl.score),1) as peak,
            ROUND(MIN(sl.score),1) as lowest
            FROM scan_log sl
            JOIN team_members tm ON sl.email=tm.email AND tm.team_id=?
            WHERE sl.scanned_at > ?
            GROUP BY sl.email""", (team_id, this_week)).fetchall()
        this_week_stats = [dict(r) for r in this_week_stats]

        if not this_week_stats:
            return {"awards": [], "team_avg": 0, "total_scans": 0}

        # Last week stats per user (for improvement calc)
        last_week_stats = db.execute("""SELECT sl.email,
            ROUND(AVG(sl.score),1) as avg
            FROM scan_log sl
            JOIN team_members tm ON sl.email=tm.email AND tm.team_id=?
            WHERE sl.scanned_at > ? AND sl.scanned_at <= ?
            GROUP BY sl.email""", (team_id, last_week_start, this_week)).fetchall()
        last_week_map = {r["email"]: r["avg"] for r in last_week_stats}

        # Team overall this week
        team_row = db.execute("""SELECT
            ROUND(AVG(sl.score),1) as team_avg,
            COUNT(*) as total_scans
            FROM scan_log sl
            JOIN team_members tm ON sl.email=tm.email AND tm.team_id=?
            WHERE sl.scanned_at > ?""", (team_id, this_week)).fetchone()

        # Team overall last week
        last_team = db.execute("""SELECT ROUND(AVG(sl.score),1) as team_avg
            FROM scan_log sl
            JOIN team_members tm ON sl.email=tm.email AND tm.team_id=?
            WHERE sl.scanned_at > ? AND sl.scanned_at <= ?""", (team_id, last_week_start, this_week)).fetchone()

        awards = []

        # 1. Zen Master — lowest average score
        zen = min(this_week_stats, key=lambda x: x["avg"])
        if zen["scans"] >= 2:
            awards.append({
                "type": "zen_master",
                "emoji": "🧘",
                "title": "Zen Master",
                "subtitle": "Most professional communicator",
                "email": zen["email"],
                "stat": str(zen["avg"]) + " avg score",
                "description": "Lowest average score this week. Suspiciously calm."
            })

        # 2. Most Improved — biggest score drop from last week
        improvements = []
        for u in this_week_stats:
            if u["email"] in last_week_map:
                drop = last_week_map[u["email"]] - u["avg"]
                if drop > 0:
                    improvements.append({**u, "improvement": round(drop, 1)})
        if improvements:
            best = max(improvements, key=lambda x: x["improvement"])
            awards.append({
                "type": "most_improved",
                "emoji": "📈",
                "title": "Most Improved",
                "subtitle": "Biggest tone improvement",
                "email": best["email"],
                "stat": "-" + str(best["improvement"]) + " points",
                "description": "Score dropped " + str(best["improvement"]) + " points from last week. Growth!"
            })

        # 3. Heat Check — highest single score (funny one-off)
        peak_user = max(this_week_stats, key=lambda x: x["peak"])
        if peak_user["peak"] >= 5:
            awards.append({
                "type": "heat_check",
                "emoji": "🔥",
                "title": "Heat Check",
                "subtitle": "Most unhinged moment",
                "email": peak_user["email"],
                "stat": str(peak_user["peak"]) + "/10 peak",
                "description": "Everyone has that one email. This was theirs."
            })

        # 4. Power User — most scans
        power = max(this_week_stats, key=lambda x: x["scans"])
        if power["scans"] >= 3:
            awards.append({
                "type": "power_user",
                "emoji": "⚡",
                "title": "Power User",
                "subtitle": "Takes communication seriously",
                "email": power["email"],
                "stat": str(power["scans"]) + " scans",
                "description": "Most scans this week. Checking every email before sending."
            })

        # 5. Team Tone trend
        team_avg = team_row["team_avg"] if team_row else 0
        last_avg = last_team["team_avg"] if last_team and last_team["team_avg"] else None
        team_trend = None
        if last_avg and team_avg:
            diff = round(last_avg - team_avg, 1)
            if diff > 0:
                team_trend = {"direction": "improving", "diff": diff}
            elif diff < 0:
                team_trend = {"direction": "worsening", "diff": abs(diff)}

        return {
            "awards": awards,
            "team_avg": team_avg,
            "total_scans": team_row["total_scans"] if team_row else 0,
            "last_week_avg": last_avg,
            "team_trend": team_trend,
            "members": this_week_stats,
        }
