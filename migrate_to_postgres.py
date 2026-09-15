"""
One-time migration: copies all existing data from the SQLite database
(DB_PATH, the old production database) into the new Postgres database
(DATABASE_URL, Neon).

Run this ONCE, after deploying the new Postgres-backed database.py but
before removing the old SQLite disk. Safe to re-run — it clears and
re-copies each table rather than appending duplicates.

Usage (from a Render Shell session, where both DB_PATH and DATABASE_URL
are available as env vars):
    python3 migrate_to_postgres.py
"""
import os, sqlite3, sys
import psycopg2
import psycopg2.extras

SQLITE_PATH = os.getenv("DB_PATH", "./data/unhinged.db")
PG_URL = os.getenv("DATABASE_URL")

if not PG_URL:
    print("ERROR: DATABASE_URL not set. Aborting — nothing was touched.")
    sys.exit(1)

if not os.path.exists(SQLITE_PATH):
    print(f"ERROR: SQLite database not found at {SQLITE_PATH}. Aborting.")
    sys.exit(1)

print(f"Source (SQLite): {SQLITE_PATH}")
print(f"Target (Postgres): {PG_URL.split('@')[-1] if '@' in PG_URL else '(hidden)'}")
print()

sconn = sqlite3.connect(SQLITE_PATH)
sconn.row_factory = sqlite3.Row
pconn = psycopg2.connect(PG_URL)
pcur = pconn.cursor()

def migrate_table(table, columns, id_column="id"):
    """Copies every row from SQLite's `table` into Postgres's `table`,
    preserving explicit id values so foreign keys (e.g. team_members.team_id
    referencing teams.id) still point at the right rows after migration."""
    rows = sconn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        print(f"  {table}: 0 rows (nothing to migrate)")
        return
    pcur.execute(f"DELETE FROM {table}")  # safe to re-run: clear before re-copying
    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    count = 0
    for row in rows:
        values = [row[c] if c in row.keys() else None for c in columns]
        pcur.execute(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", values)
        count += 1
    # Reset the SERIAL sequence so future auto-generated ids don't collide
    # with the explicit ones we just inserted.
    if id_column:
        pcur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', '{id_column}'), COALESCE((SELECT MAX({id_column}) FROM {table}), 1), true)")
    print(f"  {table}: {count} rows migrated")

print("Migrating tables (in dependency order)...")

migrate_table("users", ["id","email","is_pro","scans_used","daily_scans","daily_reset",
    "razorpay_sub_id","razorpay_pay_id","created_at","expires_at","last_scan_at",
    "credits","in_trial","trial_reminder_sent","trial_used","last_login_source","password_hash"])

migrate_table("llm_config", ["id","config_key","config_value","updated_at"])

migrate_table("teams", ["id","name","owner_email","seats","is_active","invite_code",
    "razorpay_sub_id","created_at"])

migrate_table("team_members", ["id","team_id","email","role","joined_at"])

migrate_table("scan_log", ["id","email","score","scanned_at"])

migrate_table("pending_team_members", ["id","razorpay_sub_id","email","created_at"])

migrate_table("credit_purchases", ["id","razorpay_order_id","email","credits","amount_inr",
    "status","created_at"])

# reset_tokens only exists if it was ever created (lazy CREATE TABLE) — check first
try:
    sconn.execute("SELECT 1 FROM reset_tokens LIMIT 1")
    pcur.execute("""CREATE TABLE IF NOT EXISTS reset_tokens (
        id SERIAL PRIMARY KEY, email TEXT NOT NULL, token TEXT UNIQUE NOT NULL,
        expires_at REAL NOT NULL, used INTEGER DEFAULT 0)""")
    migrate_table("reset_tokens", ["id","email","token","expires_at","used"])
except sqlite3.OperationalError:
    print("  reset_tokens: table doesn't exist yet in SQLite, skipping")

pconn.commit()
sconn.close()
pcur.close()
pconn.close()

print()
print("Migration complete. Spot-check a few real values in the Postgres")
print("database (via the admin panel or a direct query) before treating")
print("this as done — don't just trust the row counts above.")
