import logging

from flask import Flask, jsonify, render_template, request

import config
from services import db
from services.pvs6_service import PVS6Service
from services.emporia_service import EmporiaService
from services.calculator import Calculator
from services.collector import DataCollector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Initialize services
pvs6_svc = PVS6Service(
    host=config.PVS6_HOST,
    port=config.PVS6_PORT,
    use_https=config.PVS6_USE_HTTPS,
)
emporia_svc = EmporiaService(
    email=config.EMPORIA_EMAIL,
    password=config.EMPORIA_PASSWORD,
)
calculator = Calculator(electricity_rate=config.ELECTRICITY_RATE)
collector = DataCollector(
    pvs6_service=pvs6_svc,
    emporia_service=emporia_svc,
    calculator=calculator,
    poll_interval=config.POLL_INTERVAL_SECONDS,
)


# ---- Page routes ----

@app.route("/")
def index():
    return render_template("index.html", active_page="dashboard")


@app.route("/circuits")
def circuits_page():
    return render_template("circuits.html", active_page="circuits")


@app.route("/history")
def history_page():
    return render_template("history.html", active_page="history")


@app.route("/discovery")
def discovery_page():
    return render_template("discovery.html", active_page="discovery")


@app.route("/settings")
def settings_page():
    return render_template("settings.html", active_page="settings")


# ---- Real-time API ----

@app.route("/api/realtime")
def api_realtime():
    """Latest reading from collector cache (fast) or DB fallback."""
    # Prefer in-memory cache from collector (no DB hit)
    if collector.latest:
        return jsonify({**collector.latest, "status": "ok"})

    reading = db.get_latest_reading()
    if not reading:
        return jsonify({
            "solar_production_w": 0, "house_consumption_w": 0,
            "net_grid_w": 0, "crypto_mining_w": 0,
            "non_crypto_house_w": 0, "emporia_total_w": 0,
            "timestamp": None, "status": "no_data",
        })
    return jsonify({**reading, "status": "ok"})


@app.route("/api/circuits")
def api_circuits():
    """Latest per-circuit breakdown."""
    # Prefer in-memory cache from collector
    if collector.latest_circuits:
        return jsonify({
            "circuits": collector.latest_circuits,
            "reading_id": collector.latest.get("id") if collector.latest else None,
        })

    reading = db.get_latest_reading()
    if not reading:
        return jsonify({"circuits": [], "reading_id": None})
    circuits = db.get_latest_circuits(reading["id"])
    return jsonify({"circuits": circuits, "reading_id": reading["id"]})


# ---- Historical API ----

@app.route("/api/readings")
def api_readings():
    """Raw readings with optional start/end/limit."""
    start = request.args.get("start")
    end = request.args.get("end")
    limit = int(request.args.get("limit", "500"))
    rows = db.get_readings(start=start, end=end, limit=limit)
    return jsonify(rows)


@app.route("/api/hourly")
def api_hourly():
    """Hourly summaries."""
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_hourly_summaries(start=start, end=end)
    return jsonify(rows)


@app.route("/api/daily")
def api_daily():
    """Daily summaries."""
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_daily_summaries(start=start, end=end)
    return jsonify(rows)


@app.route("/api/summary")
def api_summary():
    """Today's running totals and costs."""
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    daily = db.get_daily_summaries(start=today, end=today)
    rate = float(db.get_config("electricity_rate", config.ELECTRICITY_RATE))

    if daily:
        d = daily[0]
        return jsonify({
            "date": today,
            "solar_kwh": d.get("solar_production_kwh", 0),
            "consumption_kwh": d.get("house_consumption_kwh", 0),
            "net_grid_kwh": d.get("net_grid_kwh", 0),
            "crypto_kwh": d.get("crypto_mining_kwh", 0),
            "non_crypto_kwh": d.get("non_crypto_house_kwh", 0),
            "cost": d.get("electricity_cost", 0),
            "solar_savings": d.get("solar_savings", 0),
            "rate": rate,
        })
    return jsonify({
        "date": today, "solar_kwh": 0, "consumption_kwh": 0,
        "net_grid_kwh": 0, "crypto_kwh": 0, "non_crypto_kwh": 0,
        "cost": 0, "solar_savings": 0, "rate": rate,
    })


@app.route("/api/costs")
def api_costs():
    """Cost breakdown for a period (day/week/month)."""
    period = request.args.get("period", "day")
    from datetime import datetime, timedelta
    now = datetime.utcnow()

    if period == "week":
        start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    elif period == "month":
        start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    else:
        start = now.strftime("%Y-%m-%d")

    end = now.strftime("%Y-%m-%d")
    rows = db.get_daily_summaries(start=start, end=end)

    total_cost = sum(r.get("electricity_cost", 0) or 0 for r in rows)
    total_solar_savings = sum(r.get("solar_savings", 0) or 0 for r in rows)
    total_consumption = sum(r.get("house_consumption_kwh", 0) or 0 for r in rows)
    total_crypto = sum(r.get("crypto_mining_kwh", 0) or 0 for r in rows)

    return jsonify({
        "period": period,
        "start": start,
        "end": end,
        "total_cost": round(total_cost, 2),
        "total_solar_savings": round(total_solar_savings, 2),
        "total_consumption_kwh": round(total_consumption, 2),
        "total_crypto_kwh": round(total_crypto, 2),
        "days": len(rows),
    })


# ---- Discovery API ----

@app.route("/api/discovery/scan", methods=["POST"])
def api_discovery_scan():
    """Trigger PVS6 network scan."""
    from services.discovery_service import discover_pvs6
    subnet = request.json.get("subnet", config.DISCOVERY_SUBNET) if request.json else config.DISCOVERY_SUBNET
    results = discover_pvs6(subnet)
    return jsonify({"results": results, "subnet": subnet})


# ---- Settings API ----

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    """Get current configuration."""
    return jsonify({
        "pvs6_host": db.get_config("pvs6_host", config.PVS6_HOST),
        "pvs6_port": int(db.get_config("pvs6_port", config.PVS6_PORT)),
        "pvs6_use_https": db.get_config("pvs6_use_https", str(config.PVS6_USE_HTTPS).lower()) == "true",
        "electricity_rate": float(db.get_config("electricity_rate", config.ELECTRICITY_RATE)),
        "crypto_circuit_ids": db.get_config("crypto_circuit_ids", ",".join(str(x) for x in config.CRYPTO_CIRCUIT_IDS)),
        "poll_interval": int(db.get_config("poll_interval", config.POLL_INTERVAL_SECONDS)),
        "discovery_subnet": db.get_config("discovery_subnet", config.DISCOVERY_SUBNET),
        "emporia_configured": bool(config.EMPORIA_EMAIL and config.EMPORIA_PASSWORD),
        "pvs6_configured": bool(db.get_config("pvs6_host", config.PVS6_HOST)),
    })


@app.route("/api/settings", methods=["PUT"])
def api_settings_put():
    """Update configuration values."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    allowed_keys = [
        "pvs6_host", "pvs6_port", "pvs6_use_https",
        "electricity_rate", "crypto_circuit_ids",
        "poll_interval", "discovery_subnet",
    ]
    for key in allowed_keys:
        if key in data:
            db.set_config(key, data[key])

    return jsonify({"success": True})


# ---- Status API ----

@app.route("/api/status")
def api_status():
    """Service health check."""
    reading = db.get_latest_reading()
    return jsonify({
        "pvs6_host": db.get_config("pvs6_host", config.PVS6_HOST),
        "pvs6_configured": bool(db.get_config("pvs6_host", config.PVS6_HOST)),
        "emporia_configured": bool(config.EMPORIA_EMAIL and config.EMPORIA_PASSWORD),
        "last_reading": reading["timestamp"] if reading else None,
        "db_path": str(config.DB_PATH),
    })


# ---- Startup ----

def start_collector():
    """Initialize DB and start background data collector."""
    db.init_db()

    # Seed runtime config from env defaults (only if not already set)
    if not db.get_config("electricity_rate"):
        db.set_config("electricity_rate", config.ELECTRICITY_RATE)
    if not db.get_config("pvs6_host") and config.PVS6_HOST:
        db.set_config("pvs6_host", config.PVS6_HOST)
        db.set_config("pvs6_port", config.PVS6_PORT)
        db.set_config("pvs6_use_https", str(config.PVS6_USE_HTTPS).lower())
    if not db.get_config("crypto_circuit_ids") and config.CRYPTO_CIRCUIT_IDS:
        db.set_config("crypto_circuit_ids", ",".join(str(x) for x in config.CRYPTO_CIRCUIT_IDS))
    if not db.get_config("discovery_subnet"):
        db.set_config("discovery_subnet", config.DISCOVERY_SUBNET)
    if not db.get_config("poll_interval"):
        db.set_config("poll_interval", config.POLL_INTERVAL_SECONDS)

    collector.start()
    logger.info("Electricity dashboard started on port %d", config.FLASK_PORT)


if __name__ == "__main__":
    start_collector()
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
    )
