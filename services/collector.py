import logging
import threading
import time
from datetime import datetime, timedelta

from services import db

logger = logging.getLogger(__name__)


class DataCollector:
    """Background polling thread that reads PVS6 + Emporia and writes to DB."""

    def __init__(self, pvs6_service, emporia_service, calculator, poll_interval=60, sunpower_service=None):
        self.pvs6 = pvs6_service
        self.emporia = emporia_service
        self.sunpower = sunpower_service
        self.calc = calculator
        self.poll_interval = poll_interval
        self._thread = None
        self._stop_event = threading.Event()
        self._latest = None
        self._latest_circuits = []
        self._poll_count = 0

    @property
    def latest(self):
        return self._latest

    @property
    def latest_circuits(self):
        return self._latest_circuits

    def start(self):
        """Start the background polling thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Collector already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="collector")
        self._thread.start()
        logger.info("Collector started (interval=%ds)", self.poll_interval)

    def stop(self):
        """Stop the background polling thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Collector stopped")

    def _run(self):
        """Main polling loop."""
        while not self._stop_event.is_set():
            try:
                self._poll()
            except Exception as e:
                logger.error("Collector poll error: %s", e)

            self._poll_count += 1

            # Hourly aggregation every 15 polls (~15 min at 60s interval)
            if self._poll_count % 15 == 0:
                try:
                    self._aggregate_hourly()
                except Exception as e:
                    logger.error("Hourly aggregation error: %s", e)

            # Daily aggregation + purge every 60 polls (~1 hour at 60s interval)
            if self._poll_count % 60 == 0:
                try:
                    self._aggregate_daily()
                    db.purge_old_readings()
                    db.purge_old_hourly()
                except Exception as e:
                    logger.error("Daily aggregation/purge error: %s", e)

            self._stop_event.wait(self.poll_interval)

    def _poll(self):
        """Single poll cycle: read both sources, compute metrics, write to DB."""
        # Read crypto circuit IDs from runtime config
        crypto_ids_str = db.get_config("crypto_circuit_ids", "")
        crypto_ids = [int(x.strip()) for x in crypto_ids_str.split(",") if x.strip()]

        # Update rate structure from runtime config
        for attr, key in [("energy_rate", "energy_rate"), ("demand_rate", "demand_rate"),
                          ("base_service_charge", "base_service_charge"), ("tax_rate", "tax_rate")]:
            val = db.get_config(key)
            if val:
                try:
                    setattr(self.calc, attr, float(val))
                except ValueError:
                    pass

        # Update PVS6 config from runtime settings
        pvs6_host = db.get_config("pvs6_host", "")
        if pvs6_host and pvs6_host != self.pvs6.host:
            pvs6_port = int(db.get_config("pvs6_port", "443"))
            pvs6_https = db.get_config("pvs6_use_https", "true") == "true"
            self.pvs6.update_config(pvs6_host, pvs6_port, pvs6_https)

        # Fetch solar/consumption data: try PVS6 local first, then SunPower cloud
        pvs6_data = None
        if self.pvs6.is_configured:
            pvs6_data = self.pvs6.get_both()

        # If PVS6 returned no useful data, try SunPower cloud API
        if self.sunpower and self.sunpower.is_configured:
            has_pvs6_data = pvs6_data and (pvs6_data.get("production_w", 0) or pvs6_data.get("consumption_w", 0))
            if not has_pvs6_data:
                sp_data = self.sunpower.get_current_power()
                if sp_data:
                    pvs6_data = {
                        "production_w": sp_data["production_w"],
                        "consumption_w": sp_data["consumption_w"],
                    }

        # Fetch Emporia data
        emporia_data = self.emporia.get_instant_usage(crypto_circuit_ids=crypto_ids)

        # Compute merged metrics
        metrics = self.calc.compute_realtime(pvs6_data, emporia_data)

        # Write to DB
        reading_id = db.insert_reading(
            solar_w=metrics["solar_production_w"],
            house_w=metrics["house_consumption_w"],
            net_grid_w=metrics["net_grid_w"],
            crypto_w=metrics["crypto_mining_w"],
            non_crypto_w=metrics["non_crypto_house_w"],
            emporia_total_w=metrics["emporia_total_w"],
        )

        # Write per-circuit data
        circuits = emporia_data.get("circuits", [])
        for circuit in circuits:
            is_crypto = int(circuit["channel_num"]) in crypto_ids
            db.insert_circuit_reading(
                reading_id=reading_id,
                channel_num=circuit["channel_num"],
                channel_name=circuit.get("name", ""),
                usage_w=circuit.get("usage_w", 0),
                is_crypto=is_crypto,
            )

        # Cache latest for fast API responses
        self._latest = {**metrics, "timestamp": datetime.utcnow().isoformat(), "id": reading_id}
        self._latest_circuits = circuits

        logger.debug(
            "Poll #%d: solar=%.0fW cons=%.0fW crypto=%.0fW grid=%.0fW",
            self._poll_count,
            metrics["solar_production_w"],
            metrics["house_consumption_w"],
            metrics["crypto_mining_w"],
            metrics["net_grid_w"],
        )

    def _aggregate_hourly(self):
        """Compute hourly summary from raw readings for the current hour."""
        now = datetime.utcnow()
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        hour_end = hour_start + timedelta(hours=1)

        readings = db.get_readings(
            start=hour_start.isoformat(),
            end=hour_end.isoformat(),
            limit=120,  # Max ~60 readings per hour at 60s interval
        )

        if not readings:
            return

        agg = self.calc.compute_hourly_aggregation(readings)
        if agg:
            db.upsert_hourly_summary(
                hour_start=hour_start.isoformat(),
                **agg,
            )
            logger.debug("Hourly summary updated for %s", hour_start.isoformat())

    def _aggregate_daily(self):
        """Compute daily summary from hourly summaries for today."""
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        today_start = today + "T00:00:00"
        today_end = today + "T23:59:59"

        hourly_rows = db.get_hourly_summaries(start=today_start, end=today_end)
        if not hourly_rows:
            return

        agg = self.calc.compute_daily_aggregation(hourly_rows)
        if agg:
            db.upsert_daily_summary(date_str=today, **agg)
            logger.debug("Daily summary updated for %s", today)
