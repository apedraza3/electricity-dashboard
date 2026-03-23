import logging

logger = logging.getLogger(__name__)


class Calculator:
    """Compute derived metrics from raw PVS6 + Emporia data.

    Supports CORE Electric Cooperative tiered billing:
    - Energy charge: $/kWh for all consumption
    - Demand charge: $/kW for peak demand in billing period
    - Base service charge: flat monthly fee
    - Tax rate: percentage applied to subtotal
    """

    def __init__(self, energy_rate=0.08907, demand_rate=15.38,
                 base_service_charge=20.00, tax_rate=0.0672):
        self.energy_rate = energy_rate        # $/kWh
        self.demand_rate = demand_rate        # $/kW peak
        self.base_service_charge = base_service_charge  # $/month flat
        self.tax_rate = tax_rate              # decimal (6.72% = 0.0672)

    def compute_realtime(self, pvs6_data, emporia_data):
        """Merge PVS6 and Emporia data into a single reading."""
        solar_w = 0
        house_w = 0
        crypto_w = 0
        emporia_total_w = 0

        if pvs6_data:
            solar_w = pvs6_data.get("production_w", 0) or 0
            house_w = pvs6_data.get("consumption_w", 0) or 0

        if emporia_data:
            # Emporia only monitors crypto mining circuits, not the whole house
            crypto_w = emporia_data.get("crypto_w", 0) or 0
            emporia_total_w = emporia_data.get("total_w", 0) or 0

        # PVS6 is the source of truth for house consumption
        # Emporia only tells us how much of that is crypto mining
        net_grid_w = house_w - solar_w
        non_crypto_w = house_w - crypto_w if house_w > crypto_w else 0

        return {
            "solar_production_w": round(solar_w, 1),
            "house_consumption_w": round(house_w, 1),
            "net_grid_w": round(net_grid_w, 1),
            "crypto_mining_w": round(crypto_w, 1),
            "non_crypto_house_w": round(non_crypto_w, 1),
            "emporia_total_w": round(emporia_total_w, 1),
        }

    def compute_energy_cost_per_hour(self, consumption_w):
        """Energy charge component per hour ($/hr)."""
        kwh = consumption_w / 1000
        return round(kwh * self.energy_rate, 4)

    def compute_demand_cost(self, peak_kw):
        """Demand charge component for billing period."""
        return round(peak_kw * self.demand_rate, 2)

    def compute_monthly_estimate(self, avg_consumption_w, peak_kw):
        """Estimate full monthly bill from current usage patterns.

        Returns dict with line-item breakdown matching CORE Electric bill.
        """
        hours_in_month = 730  # avg
        total_kwh = (avg_consumption_w / 1000) * hours_in_month

        energy_charge = total_kwh * self.energy_rate
        demand_charge = peak_kw * self.demand_rate
        subtotal = self.base_service_charge + energy_charge + demand_charge
        taxes = subtotal * self.tax_rate
        total = subtotal + taxes

        return {
            "base_service_charge": self.base_service_charge,
            "energy_kwh": round(total_kwh, 1),
            "energy_charge": round(energy_charge, 2),
            "peak_demand_kw": round(peak_kw, 2),
            "demand_charge": round(demand_charge, 2),
            "subtotal": round(subtotal, 2),
            "taxes": round(taxes, 2),
            "total_estimate": round(total, 2),
            "effective_rate_per_kwh": round(total / total_kwh, 5) if total_kwh > 0 else 0,
        }

    def compute_solar_offset(self, solar_w, consumption_w):
        """Calculate solar offset percentage."""
        if consumption_w <= 0:
            return 0
        return round(min((solar_w / consumption_w) * 100, 100), 1)

    def compute_hourly_aggregation(self, readings):
        """Aggregate raw readings into hourly summary values."""
        if not readings:
            return None

        n = len(readings)

        avg_solar = sum(r.get("solar_production_w", 0) or 0 for r in readings) / n
        avg_cons = sum(r.get("house_consumption_w", 0) or 0 for r in readings) / n
        avg_crypto = sum(r.get("crypto_mining_w", 0) or 0 for r in readings) / n
        avg_grid = sum(r.get("net_grid_w", 0) or 0 for r in readings) / n
        avg_non_crypto = sum(r.get("non_crypto_house_w", 0) or 0 for r in readings) / n

        solar_wh = avg_solar
        cons_wh = avg_cons
        grid_wh = avg_grid
        crypto_wh = avg_crypto
        non_crypto_wh = avg_non_crypto

        # Energy cost for this hour (only grid import)
        grid_import_w = max(avg_grid, 0)
        electricity_cost = (grid_import_w / 1000) * self.energy_rate

        # Solar savings = energy charge avoided
        solar_used_w = min(avg_solar, avg_cons)
        solar_savings = (solar_used_w / 1000) * self.energy_rate

        return {
            "solar_wh": round(solar_wh, 1),
            "house_wh": round(cons_wh, 1),
            "net_grid_wh": round(grid_wh, 1),
            "crypto_wh": round(crypto_wh, 1),
            "non_crypto_wh": round(non_crypto_wh, 1),
            "avg_solar": round(avg_solar, 1),
            "avg_consumption": round(avg_cons, 1),
            "avg_crypto": round(avg_crypto, 1),
            "electricity_cost": round(electricity_cost, 4),
            "solar_savings": round(solar_savings, 4),
        }

    def compute_daily_aggregation(self, hourly_rows):
        """Aggregate hourly summaries into a daily summary."""
        if not hourly_rows:
            return None

        # Hourly rows come from DB with column names (solar_production_wh) or from compute (solar_wh)
        def h(r, key, alt_key=None):
            return r.get(key, 0) or (r.get(alt_key, 0) if alt_key else 0) or 0

        solar_kwh = sum(h(r, "solar_production_wh", "solar_wh") for r in hourly_rows) / 1000
        cons_kwh = sum(h(r, "house_consumption_wh", "house_wh") for r in hourly_rows) / 1000
        grid_kwh = sum(h(r, "net_grid_wh") for r in hourly_rows) / 1000
        crypto_kwh = sum(h(r, "crypto_mining_wh", "crypto_wh") for r in hourly_rows) / 1000
        non_crypto_kwh = sum(h(r, "non_crypto_house_wh", "non_crypto_wh") for r in hourly_rows) / 1000

        peak_solar = max((h(r, "avg_solar_w", "avg_solar") for r in hourly_rows), default=0)
        peak_cons = max((h(r, "avg_consumption_w", "avg_consumption") for r in hourly_rows), default=0)

        # Energy costs (sum of hourly energy charges)
        total_cost = sum(r.get("electricity_cost", 0) or 0 for r in hourly_rows)
        total_savings = sum(r.get("solar_savings", 0) or 0 for r in hourly_rows)

        return {
            "solar_kwh": round(solar_kwh, 3),
            "house_kwh": round(cons_kwh, 3),
            "net_grid_kwh": round(grid_kwh, 3),
            "crypto_kwh": round(crypto_kwh, 3),
            "non_crypto_kwh": round(non_crypto_kwh, 3),
            "peak_solar": round(peak_solar, 1),
            "peak_consumption": round(peak_cons, 1),
            "electricity_cost": round(total_cost, 4),
            "solar_savings": round(total_savings, 4),
        }
