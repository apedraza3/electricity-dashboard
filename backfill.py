"""One-time backfill: aggregate existing raw readings into hourly + daily summaries."""
import sqlite3
from datetime import datetime, timedelta
from services.calculator import Calculator
from services import db

DB_PATH = db.DB_PATH

def backfill():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Get energy rate from config
    rate_row = conn.execute("SELECT value FROM config WHERE key='energy_rate'").fetchone()
    energy_rate = float(rate_row["value"]) if rate_row else 0.08907

    calc = Calculator(energy_rate=energy_rate)

    # Find date range of existing readings
    bounds = conn.execute(
        "SELECT MIN(timestamp) as mn, MAX(timestamp) as mx FROM readings"
    ).fetchone()
    if not bounds or not bounds["mn"]:
        print("No readings found.")
        return

    start = datetime.strptime(bounds["mn"][:19], "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(bounds["mx"][:19], "%Y-%m-%d %H:%M:%S")
    print(f"Readings from {start} to {end}")

    # Backfill hourly summaries
    hour = start.replace(minute=0, second=0, microsecond=0)
    hourly_count = 0
    while hour <= end:
        hour_end = hour + timedelta(hours=1)
        rows = db.get_readings(
            start=hour.strftime("%Y-%m-%d %H:%M:%S"),
            end=hour_end.strftime("%Y-%m-%d %H:%M:%S"),
            limit=120,
        )
        if rows:
            agg = calc.compute_hourly_aggregation(rows)
            if agg:
                db.upsert_hourly_summary(
                    hour_start=hour.strftime("%Y-%m-%d %H:%M:%S"),
                    **agg,
                )
                hourly_count += 1
        hour = hour_end

    print(f"Backfilled {hourly_count} hourly summaries")

    # Backfill daily summaries
    day = start.date()
    end_day = end.date()
    daily_count = 0
    while day <= end_day:
        day_str = day.strftime("%Y-%m-%d")
        hourly_rows = db.get_hourly_summaries(
            start=day_str + " 00:00:00",
            end=day_str + " 23:59:59",
        )
        if hourly_rows:
            agg = calc.compute_daily_aggregation(hourly_rows)
            if agg:
                db.upsert_daily_summary(date_str=day_str, **agg)
                daily_count += 1
        day += timedelta(days=1)

    print(f"Backfilled {daily_count} daily summaries")

    # Verify
    h = conn.execute("SELECT COUNT(*) FROM hourly_summary").fetchone()[0]
    d = conn.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0]
    total_savings = conn.execute("SELECT SUM(solar_savings) FROM daily_summary").fetchone()[0] or 0
    print(f"\nTotals: {h} hourly rows, {d} daily rows, ${total_savings:.2f} total solar savings")
    conn.close()

if __name__ == "__main__":
    backfill()
