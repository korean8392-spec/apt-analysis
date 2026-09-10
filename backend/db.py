import json
import sqlite3
import time
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_key TEXT NOT NULL,
    pyeong_label TEXT NOT NULL,
    exclusive_area REAL,
    listing_count INTEGER,
    ask_price_min REAL,
    ask_price_max REAL,
    memo TEXT,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_manual_listings_complex
    ON manual_listings(complex_key);

CREATE TABLE IF NOT EXISTS manual_complex_info (
    complex_key TEXT PRIMARY KEY,
    building_coverage_ratio REAL,
    floor_area_ratio REAL,
    household_cnt INTEGER,
    use_approval_year INTEGER,
    memo TEXT,
    updated_at REAL NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def cache_get(key: str, max_age_sec: float):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM cache WHERE cache_key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    if time.time() - row["fetched_at"] > max_age_sec:
        return None
    return json.loads(row["payload"])


def cache_set(key: str, payload):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cache(cache_key, payload, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload, "
            "fetched_at = excluded.fetched_at",
            (key, json.dumps(payload, ensure_ascii=False), time.time()),
        )


def upsert_manual_listing(
    complex_key: str,
    pyeong_label: str,
    exclusive_area: float | None,
    listing_count: int | None,
    ask_price_min: float | None,
    ask_price_max: float | None,
    memo: str | None,
):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM manual_listings WHERE complex_key = ? AND pyeong_label = ?",
            (complex_key, pyeong_label),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE manual_listings SET exclusive_area=?, listing_count=?, "
                "ask_price_min=?, ask_price_max=?, memo=?, updated_at=? WHERE id=?",
                (
                    exclusive_area,
                    listing_count,
                    ask_price_min,
                    ask_price_max,
                    memo,
                    time.time(),
                    existing["id"],
                ),
            )
        else:
            conn.execute(
                "INSERT INTO manual_listings(complex_key, pyeong_label, exclusive_area, "
                "listing_count, ask_price_min, ask_price_max, memo, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    complex_key,
                    pyeong_label,
                    exclusive_area,
                    listing_count,
                    ask_price_min,
                    ask_price_max,
                    memo,
                    time.time(),
                ),
            )


def get_manual_listings(complex_key: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM manual_listings WHERE complex_key = ? ORDER BY exclusive_area",
            (complex_key,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_manual_listing(listing_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM manual_listings WHERE id = ?", (listing_id,))


def upsert_manual_complex_info(
    complex_key: str,
    building_coverage_ratio: float | None,
    floor_area_ratio: float | None,
    household_cnt: int | None,
    use_approval_year: int | None,
    memo: str | None,
):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO manual_complex_info(complex_key, building_coverage_ratio, "
            "floor_area_ratio, household_cnt, use_approval_year, memo, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(complex_key) DO UPDATE SET "
            "building_coverage_ratio=excluded.building_coverage_ratio, "
            "floor_area_ratio=excluded.floor_area_ratio, "
            "household_cnt=excluded.household_cnt, "
            "use_approval_year=excluded.use_approval_year, "
            "memo=excluded.memo, updated_at=excluded.updated_at",
            (
                complex_key,
                building_coverage_ratio,
                floor_area_ratio,
                household_cnt,
                use_approval_year,
                memo,
                time.time(),
            ),
        )


def get_manual_complex_info(complex_key: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM manual_complex_info WHERE complex_key = ?", (complex_key,)
        ).fetchone()
    return dict(row) if row else None
