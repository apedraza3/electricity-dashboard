import logging
import threading
import time
from datetime import datetime, timedelta

from services import db

logger = logging.getLogger(__name__)


class DataCollector:
    """Background polling thread that reads PVS6 + Emporia and writes to DB."""

    def __init__(self, pvs6_service, emporia_service, calculator, poll_interval=60):
        self.pvs6 = pvs6_service
        self.emporia = emporia_service
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

        # Fetch solar/consumption data from PVS6 local API
        pvs6_data = None
        if self.pvs6.is_configured:
            pvs6_data = self.pvs6.get_both()

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
        """Compute hourly summaries, backfilling any missed hours."""
        now = datetime.utcnow()
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        # Backfill up to 24 missed hours
        for hours_ago in range(24, -1, -1):
            hour_start = current_hour - timedelta(hours=hours_ago)
            hour_end = hour_start + timedelta(hours=1)

            # Skip future hours
            if hour_start > current_hour:
                continue

            start_str = hour_start.strftime("%Y-%m-%d %H:%M:%S")
            end_str = hour_end.strftime("%Y-%m-%d %H:%M:%S")

            readings = db.get_readings(start=start_str, end=end_str, limit=120)
            if not readings:
                continue

            agg = self.calc.compute_hourly_aggregation(readings)
            if agg:
                db.upsert_hourly_summary(hour_start=start_str, **agg)

        logger.debug("Hourly aggregation complete (with backfill)")

    def _aggregate_daily(self):
        """Compute daily summaries, backfilling yesterday too."""
        now = datetime.utcnow()

        # Process yesterday and today
        for days_ago in range(1, -1, -1):
            day = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            day_start = day + " 00:00:00"
            day_end = day + " 23:59:59"

            hourly_rows = db.get_hourly_summaries(start=day_start, end=day_end)
            if not hourly_rows:
                continue

            agg = self.calc.compute_daily_aggregation(hourly_rows)
            if agg:
                db.upsert_daily_summary(date_str=day, **agg)
                logger.debug("Daily summary updated for %s", day)
