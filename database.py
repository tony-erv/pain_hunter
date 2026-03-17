import sqlite3
import hashlib
import logging
from datetime import datetime, timedelta
from config import cfg

logger = logging.getLogger(__name__)

def get_conn():
    """Return a new database connection with dict-like row access."""
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.row_factory = sqlite3.Row  # dict-like access: row["field"]
    return conn

def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                username     TEXT,
                first_name   TEXT,
                custom_niches TEXT,          -- JSON list of niches or NULL
                hunt_count   INTEGER DEFAULT 0,
                last_hunt    TEXT,           -- ISO datetime last /hunt
                created_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS pains (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER,
                niche        TEXT NOT NULL,
                title        TEXT NOT NULL,
                quote        TEXT,
                source       TEXT,
                frequency    INTEGER DEFAULT 3,
                emotion      INTEGER DEFAULT 3,
                monetizable  INTEGER DEFAULT 1,  -- 0 или 1
                score        REAL DEFAULT 3.0,
                found_at     TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS seen_hashes (
                url_hash     TEXT PRIMARY KEY,
                source_url   TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_pains_user
                ON pains(user_id);
            CREATE INDEX IF NOT EXISTS idx_pains_score
                ON pains(score DESC);
            CREATE INDEX IF NOT EXISTS idx_seen_created
                ON seen_hashes(created_at);
        """)
    logger.info("DB initialised at %s", cfg.DB_PATH)


# -- Users 
def save_user(user_id: int, username: str = "", first_name: str = ""):
    """Create or update a user. Uses UPSERT to avoid duplicates."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name
        """, (user_id, username, first_name))

def get_all_user_ids() -> list[int]:
    """Return a list of all user_ids in the users table."""
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
    return [r["user_id"] for r in rows]

def can_hunt(user_id: int) -> tuple[bool, int]:
    """
    Checking rate limit: no more MAX_HUNT_PER_HOUR in hour.
    Returned (can_hunt, minutes_to_wait).
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT hunt_count, last_hunt FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    if not row or not row["last_hunt"]:
        return True, 0

    last = datetime.fromisoformat(row["last_hunt"])
    now  = datetime.utcnow()

    # Reset count if more than an hour has passed since last hunt
    if now - last > timedelta(hours=1):
        with get_conn() as conn:
            conn.execute(
                "UPDATE users SET hunt_count = 0 WHERE user_id = ?",
                (user_id,)
            )
        return True, 0

    if row["hunt_count"] >= cfg.MAX_HUNT_PER_HOUR:
        wait = 60 - int((now - last).total_seconds() / 60)
        return False, wait

    return True, 0

def increment_hunt(user_id: int):
    """Increment the hunt count and update last_hunt timestamp for the user."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE users
            SET hunt_count = hunt_count + 1,
                last_hunt  = datetime('now')
            WHERE user_id = ?
        """, (user_id,))


# --- Pains ---------------

def save_pain(user_id: int, pain: dict) -> int:
    """
    Save a pain record to the database. Returns the new pain ID.
    """
    with get_conn() as conn:
        cursor = conn.execute("""
            INSERT INTO pains
                (user_id, niche, title, quote, source,
                 frequency, emotion, monetizable, score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            pain.get("niche", ""),
            pain.get("title", ""),
            pain.get("quote", ""),
            pain.get("source", ""),
            pain.get("frequency", 3),
            pain.get("emotion", 3),
            1 if pain.get("monetizable", True) else 0,
            pain.get("score", 3.0),
        ))
    return cursor.lastrowid

def get_saved_pains(user_id: int, limit: int = 10) -> list:
    """Return a list of saved pains for the user, ordered by found_at desc."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM pains
            WHERE user_id = ?
            ORDER BY found_at DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()
    return [dict(r) for r in rows]

def get_stats(user_id: int) -> dict:
    """ Statistics for the user: total pains, high potential pains, top niches. """
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM pains WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

        top_niches = conn.execute("""
            SELECT niche, COUNT(*) as cnt
            FROM pains WHERE user_id = ?
            GROUP BY niche ORDER BY cnt DESC LIMIT 3
        """, (user_id,)).fetchall()

        high_potential = conn.execute("""
            SELECT COUNT(*) FROM pains
            WHERE user_id = ? AND score >= 4.0 AND monetizable = 1
        """, (user_id,)).fetchone()[0]

    return {
        "total":          total,
        "high_potential": high_potential,
        "top_niches":     [dict(r) for r in top_niches],
    }

# --- Seen Hashes for deduplication -----------

def make_hash(url: str) -> str:
    """Create a hash of the URL for deduplication. Uses MD5 for simplicity."""
    return hashlib.md5(url.encode()).hexdigest()

def is_seen(url: str) -> bool:
    """True if the URL hash is already in seen_hashes, False otherwise."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_hashes WHERE url_hash = ?",
            (make_hash(url),)
        ).fetchone()
    return row is not None


def mark_seen(url: str):
    """Create a hash of the URL and insert it into seen_hashes. Uses INSERT OR IGNORE to avoid duplicates."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_hashes (url_hash, source_url) VALUES (?, ?)",
            (make_hash(url), url)
        )


def cleanup_old_hashes():
    """Delete hashes older than HASH_TTL_DAYS to prevent the table from growing indefinitely."""
    with get_conn() as conn:
        conn.execute("""
            DELETE FROM seen_hashes
            WHERE created_at < datetime('now', ? || ' days')
        """, (f"-{cfg.HASH_TTL_DAYS}",))
    logger.info("Cleaned up hashes older than %d days", cfg.HASH_TTL_DAYS)

    