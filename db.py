import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

_pool: psycopg2.pool.SimpleConnectionPool = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=os.environ["DATABASE_URL"],
        )
    return _pool


@contextmanager
def _cursor():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    finally:
        pool.putconn(conn)


def keepalive() -> None:
    """Ping the DB to prevent Neon serverless from suspending."""
    with _cursor() as c:
        c.execute("SELECT 1")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_db() -> None:
    with _cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id       BIGINT PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                company     TEXT,
                country     TEXT,
                phone       TEXT,
                status      TEXT    DEFAULT 'new',
                language    TEXT    DEFAULT 'ru',
                pending_lot INTEGER,
                created_at  TEXT    DEFAULT TO_CHAR(NOW(), 'YYYY-MM-DD"T"HH24:MI:SS')
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS lots (
                id                   SERIAL PRIMARY KEY,
                title                TEXT    NOT NULL,
                reg_number           TEXT,
                description          TEXT,
                parts                TEXT    DEFAULT '[]',
                photos               TEXT    DEFAULT '[]',
                videos               TEXT    DEFAULT '[]',
                start_price          REAL    NOT NULL,
                reserve_price        REAL,
                current_price        REAL    NOT NULL,
                bid_step             REAL    DEFAULT 10,
                starts_at            TEXT,
                end_time             TEXT    NOT NULL,
                status               TEXT    DEFAULT 'active',
                winner_id            BIGINT,
                winner_price         REAL,
                leader_user_id       BIGINT,
                channel_message_id   INTEGER,
                notified_ending_soon INTEGER DEFAULT 0,
                created_at           TEXT    DEFAULT TO_CHAR(NOW(), 'YYYY-MM-DD"T"HH24:MI:SS')
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bids (
                id         SERIAL PRIMARY KEY,
                lot_id     INTEGER NOT NULL REFERENCES lots(id),
                user_id    BIGINT  NOT NULL REFERENCES users(tg_id),
                amount     REAL    NOT NULL,
                status     TEXT    DEFAULT 'accepted',
                created_at TEXT    DEFAULT TO_CHAR(NOW(), 'YYYY-MM-DD"T"HH24:MI:SS')
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS lot_bidders (
                lot_id  INTEGER NOT NULL,
                user_id BIGINT  NOT NULL,
                num     INTEGER NOT NULL,
                PRIMARY KEY (lot_id, user_id)
            )
        """)


# ── Users ──────────────────────────────────────────────────────────────────────

def ensure_user(tg_id: int, username: str = None) -> None:
    with _cursor() as c:
        c.execute("""
            INSERT INTO users (tg_id, username) VALUES (%s, %s)
            ON CONFLICT (tg_id) DO UPDATE SET username = EXCLUDED.username
        """, (tg_id, username))


def register_user(tg_id: int, full_name: str, company: str,
                  country: str, phone: str, pending_lot: int = None) -> None:
    with _cursor() as c:
        c.execute("""
            UPDATE users
            SET full_name=%s, company=%s, country=%s, phone=%s,
                status='pending', pending_lot=%s
            WHERE tg_id=%s
        """, (full_name, company, country, phone, pending_lot, tg_id))


def get_user(tg_id: int) -> Optional[dict]:
    with _cursor() as c:
        c.execute("SELECT * FROM users WHERE tg_id=%s", (tg_id,))
        row = c.fetchone()
    return dict(row) if row else None


def set_user_status(tg_id: int, status: str) -> None:
    with _cursor() as c:
        c.execute("UPDATE users SET status=%s WHERE tg_id=%s", (status, tg_id))


def set_user_language(tg_id: int, lang: str) -> None:
    with _cursor() as c:
        c.execute("UPDATE users SET language=%s WHERE tg_id=%s", (lang, tg_id))


def get_pending_users() -> list[dict]:
    with _cursor() as c:
        c.execute("SELECT * FROM users WHERE status='pending'")
        return [dict(r) for r in c.fetchall()]


def get_all_user_ids() -> list[int]:
    with _cursor() as c:
        c.execute("SELECT tg_id FROM users")
        return [r["tg_id"] for r in c.fetchall()]


def get_all_users() -> list[dict]:
    with _cursor() as c:
        c.execute("SELECT tg_id, username, full_name, company, country, status, language, created_at FROM users ORDER BY created_at DESC")
        return [dict(r) for r in c.fetchall()]


# ── Lots ───────────────────────────────────────────────────────────────────────

def _lot(row) -> Optional[dict]:
    if not row:
        return None
    d = dict(row)
    d["photos"] = json.loads(d.get("photos") or "[]")
    d["videos"] = json.loads(d.get("videos") or "[]")
    d["parts"]  = json.loads(d.get("parts")  or "[]")
    return d


def create_lot(title: str, reg_number: str, description: str, parts: list,
               photos: list, videos: list, start_price: float, reserve_price: float,
               bid_step: float, starts_at: str, end_time: str) -> int:
    with _cursor() as c:
        c.execute("""
            INSERT INTO lots
                (title, reg_number, description, parts, photos, videos,
                 start_price, reserve_price, current_price, bid_step, starts_at, end_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (title, reg_number, description, json.dumps(parts), json.dumps(photos),
              json.dumps(videos), start_price, reserve_price, start_price, bid_step,
              starts_at, end_time))
        return c.fetchone()["id"]


def set_lot_channel_message(lot_id: int, message_id: int) -> None:
    with _cursor() as c:
        c.execute("UPDATE lots SET channel_message_id=%s WHERE id=%s", (message_id, lot_id))


def get_lot(lot_id: int) -> Optional[dict]:
    with _cursor() as c:
        c.execute("SELECT * FROM lots WHERE id=%s", (lot_id,))
        row = c.fetchone()
    return _lot(row)


def get_active_lots() -> list[dict]:
    with _cursor() as c:
        c.execute("SELECT * FROM lots WHERE status='active' ORDER BY end_time ASC")
        return [_lot(r) for r in c.fetchall()]


def get_ended_lots() -> list[dict]:
    with _cursor() as c:
        c.execute(
            "SELECT * FROM lots WHERE status IN ('ended','unsold') ORDER BY created_at DESC"
        )
        return [_lot(r) for r in c.fetchall()]


def delete_lot(lot_id: int) -> None:
    with _cursor() as c:
        c.execute("UPDATE lots SET status='deleted' WHERE id=%s", (lot_id,))


def end_lot(lot_id: int, winner_id: Optional[int],
            winner_price: float, status: str = 'ended') -> None:
    with _cursor() as c:
        c.execute(
            "UPDATE lots SET status=%s, winner_id=%s, winner_price=%s WHERE id=%s",
            (status, winner_id, winner_price, lot_id),
        )


def update_lot_end_time(lot_id: int, new_end: str) -> None:
    with _cursor() as c:
        c.execute("UPDATE lots SET end_time=%s WHERE id=%s", (new_end, lot_id))


def get_expired_lots() -> list[dict]:
    now = _now()
    with _cursor() as c:
        c.execute(
            "SELECT * FROM lots WHERE status='active' AND end_time<=%s", (now,)
        )
        return [_lot(r) for r in c.fetchall()]


def get_lots_ending_soon(minutes: int = 6) -> list[dict]:
    now = datetime.now()
    threshold = (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    now_str = now.isoformat(timespec="seconds")
    with _cursor() as c:
        c.execute("""
            SELECT * FROM lots
            WHERE status='active'
              AND notified_ending_soon=0
              AND end_time > %s
              AND end_time <= %s
        """, (now_str, threshold))
        return [_lot(r) for r in c.fetchall()]


def mark_notified_ending_soon(lot_id: int) -> None:
    with _cursor() as c:
        c.execute("UPDATE lots SET notified_ending_soon=1 WHERE id=%s", (lot_id,))


# ── Bids ───────────────────────────────────────────────────────────────────────

def get_bidder_num(lot_id: int, user_id: int) -> int:
    with _cursor() as c:
        c.execute(
            "SELECT num FROM lot_bidders WHERE lot_id=%s AND user_id=%s",
            (lot_id, user_id),
        )
        row = c.fetchone()
        if row:
            return row["num"]
        c.execute(
            "SELECT COUNT(*) AS cnt FROM lot_bidders WHERE lot_id=%s", (lot_id,)
        )
        num = c.fetchone()["cnt"] + 1
        c.execute(
            "INSERT INTO lot_bidders (lot_id, user_id, num) VALUES (%s, %s, %s)",
            (lot_id, user_id, num),
        )
        return num


def place_bid(lot_id: int, user_id: int, amount: float) -> int:
    num = get_bidder_num(lot_id, user_id)
    with _cursor() as c:
        c.execute(
            "UPDATE lots SET current_price=%s, leader_user_id=%s WHERE id=%s",
            (amount, user_id, lot_id),
        )
        c.execute(
            "INSERT INTO bids (lot_id, user_id, amount) VALUES (%s, %s, %s)",
            (lot_id, user_id, amount),
        )
    return num


def get_last_bid(lot_id: int) -> Optional[dict]:
    with _cursor() as c:
        c.execute("""
            SELECT b.*, u.username, u.full_name, lb.num AS bidder_num
            FROM bids b
            LEFT JOIN users u ON b.user_id = u.tg_id
            LEFT JOIN lot_bidders lb ON lb.lot_id = b.lot_id AND lb.user_id = b.user_id
            WHERE b.lot_id=%s ORDER BY b.id DESC LIMIT 1
        """, (lot_id,))
        row = c.fetchone()
    return dict(row) if row else None


def get_user_bids_on_active_lots(user_id: int) -> list[dict]:
    with _cursor() as c:
        c.execute("""
            SELECT
                l.id, l.title, l.reg_number, l.current_price, l.end_time,
                l.bid_step,
                (SELECT MAX(b2.amount) FROM bids b2
                 WHERE b2.lot_id=l.id AND b2.user_id=%s) AS my_best_bid,
                l.leader_user_id
            FROM lots l
            WHERE l.status='active'
              AND EXISTS (SELECT 1 FROM bids WHERE lot_id=l.id AND user_id=%s)
            ORDER BY l.end_time ASC
        """, (user_id, user_id))
        return [dict(r) for r in c.fetchall()]

