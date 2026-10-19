import logging
import os
from functools import wraps

import bcrypt
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

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
app.secret_key = config.SECRET_KEY or os.urandom(32)

# Hash the password once at startup if auth is enabled
_password_hash = None
_auth_enabled = bool(config.DASHBOARD_PASSWORD)
if _auth_enabled:
    _password_hash = bcrypt.hashpw(
        config.DASHBOARD_PASSWORD.encode("utf-8"), bcrypt.gensalt()
    )
    logger.info("Dashboard authentication enabled (user: %s)", config.DASHBOARD_USERNAME)
else:
    logger.info("Dashboard authentication disabled (no DASHBOARD_PASSWORD set)")


def login_required(f):
    """Decorator that redirects to /login if auth is enabled and user is not logged in."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if _auth_enabled and not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

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
calculator = Calculator(
    energy_rate=config.ENERGY_RATE,
    wpca_rate=config.WPCA_RATE,
    demand_rate=config.DEMAND_RATE,
    base_service_charge=config.BASE_SERVICE_CHARGE,
    tax_rate=config.TAX_RATE,
)
collector = DataCollector(
    pvs6_service=pvs6_svc,
    emporia_service=emporia_svc,
    calculator=calculator,
    poll_interval=config.POLL_INTERVAL_SECONDS,
)


# ---- Auth routes ----

@app.route("/login", methods=["GET", "POST"])
def login():
    if not _auth_enabled:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if (
            username == config.DASHBOARD_USERNAME
            and bcrypt.checkpw(password.encode("utf-8"), _password_hash)
        ):
            session["authenticated"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid username or password")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---- Page routes ----

@app.route("/")
@login_required
def index():
    return render_template("index.html", active_page="dashboard")


@app.route("/circuits")
@login_required
def circuits_page():
    return render_template("circuits.html", active_page="circuits")


@app.route("/history")
@login_required
def history_page():
    return render_template("history.html", active_page="history")


@app.route("/discovery")
@login_required
def discovery_page():
    return render_template("discovery.html", active_page="discovery")


@app.route("/solar")
@login_required
def solar_page():
    return render_template("solar.html", active_page="solar")


@app.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html", active_page="settings")


# ---- Real-time API ----

@app.route("/api/realtime")
@login_required
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
@login_required
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
@login_required
def api_readings():
    """Raw readings with optional start/end/limit."""
    start = request.args.get("start")
    end = request.args.get("end")
    limit = int(request.args.get("limit", "500"))
    rows = db.get_readings(start=start, end=end, limit=limit)
    return jsonify(rows)


@app.route("/api/hourly")
@login_required
def api_hourly():
    """Hourly summaries."""
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_hourly_summaries(start=start, end=end)
    return jsonify(rows)


@app.route("/api/daily")
@login_required
def api_daily():
    """Daily summaries."""
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_daily_summaries(start=start, end=end)
    return jsonify(rows)


@app.route("/api/summary")
@login_required
def api_summary():
    """Today's running totals and costs."""
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    daily = db.get_daily_summaries(start=today, end=today)
    if daily:
        d = daily[0]
        return jsonify({
            "date": today,
            "solar_kwh": d.get("solar_production_kwh", 0),
            "consumption_kwh": d.get("house_consumption_kwh", 0),
            "net_grid_kwh": d.get("net_grid_kwh", 0),
            "crypto_kwh": d.get("crypto_mining_kwh", 0),
            "non_crypto_kwh": d.get("non_crypto_house_kwh", 0),
            "energy_cost": d.get("electricity_cost", 0),
            "solar_savings": d.get("solar_savings", 0),
            **_rate_fields(),
        })
    return jsonify({
        "date": today, "solar_kwh": 0, "consumption_kwh": 0,
        "net_grid_kwh": 0, "crypto_kwh": 0, "non_crypto_kwh": 0,
        "energy_cost": 0, "solar_savings": 0,
        **_rate_fields(),
    })


def _rate_fields():
    """Rate structure as the collector currently applies it."""
    return {
        "energy_rate": calculator.energy_rate,
        "wpca_rate": calculator.wpca_rate,
        "demand_rate": calculator.demand_rate,
        "tax_rate": calculator.tax_rate,
        "marginal_energy_rate": round(calculator.marginal_energy_rate, 5),
        "demand_rate_with_tax": round(calculator.demand_rate_with_tax, 2),
    }


@app.route("/api/costs")
@login_required
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
    total_solar = sum(r.get("solar_production_kwh", 0) or 0 for r in rows)

    return jsonify({
        "period": period,
        "start": start,
        "end": end,
        "total_cost": round(total_cost, 2),
        "total_solar_savings": round(total_solar_savings, 2),
        "total_consumption_kwh": round(total_consumption, 2),
        "total_crypto_kwh": round(total_crypto, 2),
        "total_solar_kwh": round(total_solar, 2),
        "days": len(rows),
    })


def _billing_period_bounds(now_utc):
    """Approximate CORE's billing cycle as the local calendar month (meters are read
    around the 1st). Returns (start_utc_naive, hours_in_period)."""
    from datetime import timedelta, timezone
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(config.LOCAL_TIMEZONE)
    except Exception:
        logger.warning("Timezone %s unavailable; using UTC month", config.LOCAL_TIMEZONE)
        tz = timezone.utc
    local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    start_local = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_local = (start_local + timedelta(days=32)).replace(day=1)
    hours = (next_local - start_local).total_seconds() / 3600
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, hours


@app.route("/api/bill-estimate")
@login_required
def api_bill_estimate():
    """Monthly bill estimate for the current billing period, matching CORE Electric format.

    Energy: CORE bills the Net register (consumption minus export), so this projects
    month-to-date net grid kWh to the full period.
    Demand: highest clock-hour average grid import this period. CORE bills the highest
    60-minute average at any hour, possibly on a rolling window, so this is a lower bound.
    """
    from datetime import datetime
    now = datetime.utcnow()
    period_start, period_hours = _billing_period_bounds(now)
    rows = db.get_hourly_summaries(start=period_start.strftime("%Y-%m-%d %H:%M:%S"))
    grid_rows = [r for r in rows if r.get("net_grid_wh") is not None]

    if grid_rows:
        measured_kwh = sum(r["net_grid_wh"] for r in grid_rows) / 1000
        projected_kwh = measured_kwh / len(grid_rows) * period_hours
        peak_kw = max(max(r["net_grid_wh"], 0) for r in grid_rows) / 1000
    else:
        # No hourly data yet this period: fall back to the latest reading
        reading = collector.latest or db.get_latest_reading() or {}
        net_grid_w = reading.get("net_grid_w") or 0
        measured_kwh = 0
        projected_kwh = max(net_grid_w, 0) / 1000 * period_hours
        peak_kw = max(net_grid_w, 0) / 1000

    estimate = calculator.compute_monthly_estimate(projected_kwh, peak_kw)
    estimate.update({
        "period_start_utc": period_start.isoformat(),
        "hours_measured": len(grid_rows),
        "hours_in_period": round(period_hours, 1),
        "measured_net_kwh": round(measured_kwh, 1),
        "peak_basis": "max clock-hour average grid import (lower bound of CORE 60-min demand)",
    })
    return jsonify(estimate)


# ---- Discovery API ----

@app.route("/api/discovery/scan", methods=["POST"])
@login_required
def api_discovery_scan():
    """Trigger PVS6 network scan."""
    from services.discovery_service import discover_pvs6
    subnet = request.json.get("subnet", config.DISCOVERY_SUBNET) if request.json else config.DISCOVERY_SUBNET
    results = discover_pvs6(subnet)
    return jsonify({"results": results, "subnet": subnet})


# ---- Settings API ----

@app.route("/api/settings", methods=["GET"])
@login_required
def api_settings_get():
    """Get current configuration."""
    return jsonify({
        "pvs6_host": db.get_config("pvs6_host", config.PVS6_HOST),
        "pvs6_port": int(db.get_config("pvs6_port", config.PVS6_PORT)),
        "pvs6_use_https": db.get_config("pvs6_use_https", str(config.PVS6_USE_HTTPS).lower()) == "true",
        "energy_rate": float(db.get_config("energy_rate", config.ENERGY_RATE)),
        "wpca_rate": float(db.get_config("wpca_rate", config.WPCA_RATE)),
        "demand_rate": float(db.get_config("demand_rate", config.DEMAND_RATE)),
        "base_service_charge": float(db.get_config("base_service_charge", config.BASE_SERVICE_CHARGE)),
        "tax_rate": float(db.get_config("tax_rate", config.TAX_RATE)),
        "crypto_circuit_ids": db.get_config("crypto_circuit_ids", ",".join(str(x) for x in config.CRYPTO_CIRCUIT_IDS)),
        "poll_interval": int(db.get_config("poll_interval", config.POLL_INTERVAL_SECONDS)),
        "discovery_subnet": db.get_config("discovery_subnet", config.DISCOVERY_SUBNET),
        "emporia_configured": bool(config.EMPORIA_EMAIL and config.EMPORIA_PASSWORD),
        "pvs6_configured": bool(db.get_config("pvs6_host", config.PVS6_HOST)),
    })


@app.route("/api/settings", methods=["PUT"])
@login_required
def api_settings_put():
    """Update configuration values."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    allowed_keys = [
        "pvs6_host", "pvs6_port", "pvs6_use_https",
        "energy_rate", "wpca_rate", "demand_rate", "base_service_charge", "tax_rate",
        "crypto_circuit_ids", "poll_interval", "discovery_subnet",
    ]
    for key in allowed_keys:
        if key in data:
            db.set_config(key, data[key])

    return jsonify({"success": True})


# ---- Solar ROI API ----

@app.route("/api/solar/config", methods=["GET"])
@login_required
def api_solar_config_get():
    """Get solar system configuration."""
    return jsonify({
        "system_cost": float(db.get_config("solar_system_cost", "0")),
        "install_date": db.get_config("solar_install_date", ""),
        "system_size_kw": float(db.get_config("solar_system_size_kw", "0")),
        "panel_count": int(db.get_config("solar_panel_count", "0")),
        "inverter": db.get_config("solar_inverter", ""),
        "incentives": float(db.get_config("solar_incentives", "0")),
    })


@app.route("/api/solar/config", methods=["PUT"])
@login_required
def api_solar_config_put():
    """Update solar system configuration."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    solar_keys = [
        "solar_system_cost", "solar_install_date", "solar_system_size_kw",
        "solar_panel_count", "solar_inverter", "solar_incentives",
    ]
    for key in solar_keys:
        if key in data:
            db.set_config(key, data[key])
    return jsonify({"success": True})


@app.route("/api/solar/production", methods=["GET"])
@login_required
def api_solar_production():
    """Get monthly solar production entries (manual + auto)."""
    return jsonify(db.get_solar_production())


@app.route("/api/solar/production", methods=["POST"])
@login_required
def api_solar_production_add():
    """Add a manual monthly solar production entry."""
    data = request.get_json()
    if not data or "month" not in data or "production_kwh" not in data:
        return jsonify({"error": "month and production_kwh required"}), 400
    mid = db.add_solar_production(
        month=data["month"],
        production_kwh=float(data["production_kwh"]),
        source=data.get("source", "manual"),
    )
    return jsonify({"id": mid, "success": True}), 201


@app.route("/api/solar/production/<int:entry_id>", methods=["DELETE"])
@login_required
def api_solar_production_delete(entry_id):
    """Delete a manual solar production entry."""
    db.delete_solar_production(entry_id)
    return jsonify({"success": True})


@app.route("/api/solar/roi")
@login_required
def api_solar_roi():
    """Calculate solar ROI summary."""
    from datetime import datetime

    system_cost = float(db.get_config("solar_system_cost", "0"))
    incentives = float(db.get_config("solar_incentives", "0"))
    install_date = db.get_config("solar_install_date", "")
    energy_rate = float(db.get_config("energy_rate", config.ENERGY_RATE))

    net_cost = system_cost - incentives

    # Get all manual production entries
    production_entries = db.get_solar_production()
    total_production_kwh = sum(e["production_kwh"] for e in production_entries)

    # Also add any auto-collected daily_summary solar data
    daily_rows = db.get_daily_summaries()
    auto_solar_kwh = sum(r.get("solar_production_kwh", 0) or 0 for r in daily_rows)
    auto_savings = sum(r.get("solar_savings", 0) or 0 for r in daily_rows)

    # Total savings = manual production * rate + auto-tracked savings
    manual_savings = total_production_kwh * energy_rate
    total_savings = manual_savings + auto_savings
    total_kwh = total_production_kwh + auto_solar_kwh

    # Calculate months since install
    months_active = 0
    if install_date:
        try:
            install_dt = datetime.strptime(install_date, "%Y-%m-%d")
            delta = datetime.utcnow() - install_dt
            months_active = max(delta.days / 30.44, 1)
        except ValueError:
            pass

    avg_monthly_savings = total_savings / months_active if months_active > 0 else 0
    avg_monthly_kwh = total_kwh / months_active if months_active > 0 else 0
    payback_pct = (total_savings / net_cost * 100) if net_cost > 0 else 0
    remaining = net_cost - total_savings
    months_to_payback = remaining / avg_monthly_savings if avg_monthly_savings > 0 else 0
    annual_roi = (avg_monthly_savings * 12 / net_cost * 100) if net_cost > 0 else 0

    return jsonify({
        "system_cost": system_cost,
        "incentives": incentives,
        "net_cost": net_cost,
        "install_date": install_date,
        "months_active": round(months_active, 1),
        "total_production_kwh": round(total_kwh, 1),
        "total_savings": round(total_savings, 2),
        "avg_monthly_savings": round(avg_monthly_savings, 2),
        "avg_monthly_kwh": round(avg_monthly_kwh, 1),
        "payback_pct": round(min(payback_pct, 100), 1),
        "remaining_to_payback": round(max(remaining, 0), 2),
        "months_to_payback": round(months_to_payback, 0),
        "annual_roi_pct": round(annual_roi, 1),
        "energy_rate": energy_rate,
        "production_entries": production_entries,
    })


# ---- Status API ----

@app.route("/api/pvs6/test", methods=["POST"])
@login_required
def api_pvs6_test():
    """Make one real request to the PVS6 with the saved settings."""
    probe_svc = PVS6Service(
        host=db.get_config("pvs6_host", config.PVS6_HOST),
        port=int(db.get_config("pvs6_port", config.PVS6_PORT)),
        use_https=db.get_config("pvs6_use_https", str(config.PVS6_USE_HTTPS).lower()) == "true",
    )
    return jsonify(probe_svc.probe())


@app.route("/api/status")
@login_required
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
    if not db.get_config("energy_rate"):
        db.set_config("energy_rate", config.ENERGY_RATE)
        db.set_config("demand_rate", config.DEMAND_RATE)
        db.set_config("base_service_charge", config.BASE_SERVICE_CHARGE)
        db.set_config("tax_rate", config.TAX_RATE)
    if not db.get_config("wpca_rate"):
        db.set_config("wpca_rate", config.WPCA_RATE)
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
