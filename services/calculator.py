import logging

logger = logging.getLogger(__name__)


class Calculator:
    """Compute derived metrics from raw PVS6 + Emporia data."""

    def __init__(self, electricity_rate=0.12):
        self.electricity_rate = electricity_rate

    def compute_realtime(self, pvs6_data, emporia_data):
        """Merge PVS6 and Emporia data into a single reading.

        Args:
            pvs6_data: dict from PVS6Service.get_both() or None
            emporia_data: dict from EmporiaService.get_instant_usage() or None

        Returns dict with all metrics in watts.
        """
        solar_w = 0
        house_w = 0
        crypto_w = 0
        emporia_total_w = 0

        if pvs6_data:
            solar_w = pvs6_data.get("production_w", 0) or 0
            house_w = pvs6_data.get("consumption_w", 0) or 0

        if emporia_data:
            crypto_w = emporia_data.get("crypto_w", 0) or 0
            emporia_total_w = emporia_data.get("total_w", 0) or 0

            # If PVS6 has no consumption data, use Emporia total as house consumption
            if house_w == 0 and emporia_total_w > 0:
                house_w = emporia_total_w

        # Derived metrics
        net_grid_w = house_w - solar_w  # positive = importing, negative = exporting
        non_crypto_w = house_w - crypto_w if house_w > crypto_w else 0

        return {
            "solar_production_w": round(solar_w, 1),
            "house_consumption_w": round(house_w, 1),
            "net_grid_w": round(net_grid_w, 1),
            "crypto_mining_w": round(crypto_w, 1),
            "non_crypto_house_w": round(non_crypto_w, 1),
            "emporia_total_w": round(emporia_total_w, 1),
        }

    def compute_cost_per_hour(self, consumption_w):
        """Calculate electricity cost per hour."""
        kw = consumption_w / 1000
        return round(kw * self.electricity_rate, 4)

    def compute_solar_offset(self, solar_w, consumption_w):
        """Calculate solar offset percentage."""
        if consumption_w <= 0:
            return 0
        return round(min((solar_w / consumption_w) * 100, 100), 1)

    def compute_hourly_aggregation(self, readings):
        """Aggregate a list of raw readings into hourly summary values.

        Args:
            readings: list of reading dicts from the DB

        Returns dict with Wh values and averages.
        """
        if not readings:
            return None

        n = len(readings)
        # Each reading represents ~POLL_INTERVAL seconds of data
        # Convert average watts to Wh: avg_W * (interval_hours)
        # For 60s polls over an hour, we have ~60 readings
        # Wh = avg_watts * 1 (hour)

        avg_solar = sum(r.get("solar_production_w", 0) or 0 for r in readings) / n
        avg_cons = sum(r.get("house_consumption_w", 0) or 0 for r in readings) / n
        avg_crypto = sum(r.get("crypto_mining_w", 0) or 0 for r in readings) / n
        avg_grid = sum(r.get("net_grid_w", 0) or 0 for r in readings) / n
        avg_non_crypto = sum(r.get("non_crypto_house_w", 0) or 0 for r in readings) / n

        # Wh = average watts * 1 hour
        solar_wh = avg_solar
        cons_wh = avg_cons
        grid_wh = avg_grid
        crypto_wh = avg_crypto
        non_crypto_wh = avg_non_crypto

        # Cost for this hour
        # Only charge for grid import (positive net_grid means importing)
        grid_import_w = max(avg_grid, 0)
        electricity_cost = (grid_import_w / 1000) * self.electricity_rate

        # Solar savings = what we would have paid without solar
        solar_used_w = min(avg_solar, avg_cons)  # solar actually offsetting consumption
        solar_savings = (solar_used_w / 1000) * self.electricity_rate

        return {
            "solar_production_wh": round(solar_wh, 1),
            "house_consumption_wh": round(cons_wh, 1),
            "net_grid_wh": round(grid_wh, 1),
            "crypto_mining_wh": round(crypto_wh, 1),
            "non_crypto_house_wh": round(non_crypto_wh, 1),
            "avg_solar_w": round(avg_solar, 1),
            "avg_consumption_w": round(avg_cons, 1),
            "avg_crypto_w": round(avg_crypto, 1),
            "electricity_cost": round(electricity_cost, 4),
            "solar_savings": round(solar_savings, 4),
        }

    def compute_daily_aggregation(self, hourly_rows):
        """Aggregate hourly summaries into a daily summary.

        Args:
            hourly_rows: list of hourly_summary dicts from DB

        Returns dict with kWh values and peaks.
        """
        if not hourly_rows:
            return None

        solar_kwh = sum(r.get("solar_production_wh", 0) or 0 for r in hourly_rows) / 1000
        cons_kwh = sum(r.get("house_consumption_wh", 0) or 0 for r in hourly_rows) / 1000
        grid_kwh = sum(r.get("net_grid_wh", 0) or 0 for r in hourly_rows) / 1000
        crypto_kwh = sum(r.get("crypto_mining_wh", 0) or 0 for r in hourly_rows) / 1000
        non_crypto_kwh = sum(r.get("non_crypto_house_wh", 0) or 0 for r in hourly_rows) / 1000

        peak_solar = max((r.get("avg_solar_w", 0) or 0 for r in hourly_rows), default=0)
        peak_cons = max((r.get("avg_consumption_w", 0) or 0 for r in hourly_rows), default=0)

        total_cost = sum(r.get("electricity_cost", 0) or 0 for r in hourly_rows)
        total_savings = sum(r.get("solar_savings", 0) or 0 for r in hourly_rows)

        return {
            "solar_production_kwh": round(solar_kwh, 3),
            "house_consumption_kwh": round(cons_kwh, 3),
            "net_grid_kwh": round(grid_kwh, 3),
            "crypto_mining_kwh": round(crypto_kwh, 3),
            "non_crypto_house_kwh": round(non_crypto_kwh, 3),
            "peak_solar_w": round(peak_solar, 1),
            "peak_consumption_w": round(peak_cons, 1),
            "electricity_cost": round(total_cost, 4),
            "solar_savings": round(total_savings, 4),
        }
