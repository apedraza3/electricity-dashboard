import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript(SCHEMA)
    logger.info("Database initialized at %s", config.DB_PATH)


@contextmanager
def get_db():
    """Yield a SQLite connection with WAL mode and foreign keys."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_reading(solar_w, house_w, net_grid_w, crypto_w, non_crypto_w, emporia_total_w):
    """Insert a raw reading and return its ID."""
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO readings
               (solar_production_w, house_consumption_w, net_grid_w,
                crypto_mining_w, non_crypto_house_w, emporia_total_w)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (solar_w, house_w, net_grid_w, crypto_w, non_crypto_w, emporia_total_w),
        )
        return cursor.lastrowid


def insert_circuit_reading(reading_id, channel_num, channel_name, usage_w, is_crypto):
    """Insert a per-circuit reading."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO circuit_readings
               (reading_id, channel_num, channel_name, usage_w, is_crypto)
               VALUES (?, ?, ?, ?, ?)""",
            (reading_id, channel_num, channel_name, usage_w, 1 if is_crypto else 0),
        )


def get_latest_reading():
    """Get the most recent reading."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_latest_circuits(reading_id):
    """Get circuit readings for a specific reading."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM circuit_readings WHERE reading_id = ?", (reading_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_readings(start=None, end=None, limit=500):
    """Get raw readings for a time range."""
    with get_db() as conn:
        query = "SELECT * FROM readings WHERE 1=1"
        params = []
        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_hourly_summaries(start=None, end=None):
    """Get hourly summaries for a time range."""
    with get_db() as conn:
        query = "SELECT * FROM hourly_summary WHERE 1=1"
        params = []
        if start:
            query += " AND hour_start >= ?"
            params.append(start)
        if end:
            query += " AND hour_start <= ?"
            params.append(end)
        query += " ORDER BY hour_start DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_daily_summaries(start=None, end=None):
    """Get daily summaries for a time range."""
    with get_db() as conn:
        query = "SELECT * FROM daily_summary WHERE 1=1"
        params = []
        if start:
            query += " AND date >= ?"
            params.append(start)
        if end:
            query += " AND date <= ?"
            params.append(end)
        query += " ORDER BY date DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def upsert_hourly_summary(hour_start, solar_wh, house_wh, net_grid_wh,
                           crypto_wh, non_crypto_wh, avg_solar, avg_consumption,
                           avg_crypto, electricity_cost, solar_savings):
    """Insert or update an hourly summary."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO hourly_summary
               (hour_start, solar_production_wh, house_consumption_wh, net_grid_wh,
                crypto_mining_wh, non_crypto_house_wh, avg_solar_w, avg_consumption_w,
                avg_crypto_w, electricity_cost, solar_savings)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(hour_start) DO UPDATE SET
                solar_production_wh=excluded.solar_production_wh,
                house_consumption_wh=excluded.house_consumption_wh,
                net_grid_wh=excluded.net_grid_wh,
                crypto_mining_wh=excluded.crypto_mining_wh,
                non_crypto_house_wh=excluded.non_crypto_house_wh,
                avg_solar_w=excluded.avg_solar_w,
                avg_consumption_w=excluded.avg_consumption_w,
                avg_crypto_w=excluded.avg_crypto_w,
                electricity_cost=excluded.electricity_cost,
                solar_savings=excluded.solar_savings""",
            (hour_start, solar_wh, house_wh, net_grid_wh, crypto_wh, non_crypto_wh,
             avg_solar, avg_consumption, avg_crypto, electricity_cost, solar_savings),
        )


def upsert_daily_summary(date_str, solar_kwh, house_kwh, net_grid_kwh,
                          crypto_kwh, non_crypto_kwh, peak_solar, peak_consumption,
                          electricity_cost, solar_savings):
    """Insert or update a daily summary."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO daily_summary
               (date, solar_production_kwh, house_consumption_kwh, net_grid_kwh,
                crypto_mining_kwh, non_crypto_house_kwh, peak_solar_w, peak_consumption_w,
                electricity_cost, solar_savings)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                solar_production_kwh=excluded.solar_production_kwh,
                house_consumption_kwh=excluded.house_consumption_kwh,
                net_grid_kwh=excluded.net_grid_kwh,
                crypto_mining_kwh=excluded.crypto_mining_kwh,
                non_crypto_house_kwh=excluded.non_crypto_house_kwh,
                peak_solar_w=excluded.peak_solar_w,
                peak_consumption_w=excluded.peak_consumption_w,
                electricity_cost=excluded.electricity_cost,
                solar_savings=excluded.solar_savings""",
            (date_str, solar_kwh, house_kwh, net_grid_kwh, crypto_kwh, non_crypto_kwh,
             peak_solar, peak_consumption, electricity_cost, solar_savings),
        )


def purge_old_readings(days=None):
    """Delete raw readings older than N days."""
    if days is None:
        days = config.RAW_RETENTION_DAYS
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_db() as conn:
        # Delete circuit readings first (FK constraint)
        conn.execute(
            """DELETE FROM circuit_readings WHERE reading_id IN
               (SELECT id FROM readings WHERE timestamp < ?)""",
            (cutoff,),
        )
        result = conn.execute("DELETE FROM readings WHERE timestamp < ?", (cutoff,))
        logger.info("Purged %d old readings (older than %d days)", result.rowcount, days)


def purge_old_hourly(days=None):
    """Delete hourly summaries older than N days."""
    if days is None:
        days = config.HOURLY_RETENTION_DAYS
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_db() as conn:
        result = conn.execute("DELETE FROM hourly_summary WHERE hour_start < ?", (cutoff,))
        logger.info("Purged %d old hourly summaries", result.rowcount)


def get_config(key, default=None):
    """Get a runtime config value."""
    with get_db() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_config(key, value):
    """Set a runtime config value."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def get_solar_production():
    """Get all manual solar production entries."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM solar_production ORDER BY month DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def add_solar_production(month, production_kwh, source="manual"):
    """Add a monthly solar production entry."""
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO solar_production (month, production_kwh, source)
               VALUES (?, ?, ?)
               ON CONFLICT(month) DO UPDATE SET
                production_kwh=excluded.production_kwh,
                source=excluded.source""",
            (month, production_kwh, source),
        )
        return cursor.lastrowid


def delete_solar_production(entry_id):
    """Delete a solar production entry."""
    with get_db() as conn:
        conn.execute("DELETE FROM solar_production WHERE id = ?", (entry_id,))


SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    solar_production_w REAL,
    house_consumption_w REAL,
    net_grid_w REAL,
    crypto_mining_w REAL,
    non_crypto_house_w REAL,
    emporia_total_w REAL
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(timestamp);

CREATE TABLE IF NOT EXISTS circuit_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reading_id INTEGER NOT NULL REFERENCES readings(id),
    channel_num INTEGER NOT NULL,
    channel_name TEXT,
    usage_w REAL,
    is_crypto INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_circuit_reading_id ON circuit_readings(reading_id);

CREATE TABLE IF NOT EXISTS hourly_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour_start TEXT NOT NULL UNIQUE,
    solar_production_wh REAL,
    house_consumption_wh REAL,
    net_grid_wh REAL,
    crypto_mining_wh REAL,
    non_crypto_house_wh REAL,
    avg_solar_w REAL,
    avg_consumption_w REAL,
    avg_crypto_w REAL,
    electricity_cost REAL,
    solar_savings REAL
);
CREATE INDEX IF NOT EXISTS idx_hourly_ts ON hourly_summary(hour_start);

CREATE TABLE IF NOT EXISTS daily_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    solar_production_kwh REAL,
    house_consumption_kwh REAL,
    net_grid_kwh REAL,
    crypto_mining_kwh REAL,
    non_crypto_house_kwh REAL,
    peak_solar_w REAL,
    peak_consumption_w REAL,
    electricity_cost REAL,
    solar_savings REAL
);
CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_summary(date);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS solar_production (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL UNIQUE,
    production_kwh REAL NOT NULL,
    source TEXT DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_solar_prod_month ON solar_production(month);
"""
