import logging

logger = logging.getLogger(__name__)


def _avg(values):
    """Average of the non-None values, or None if there are none."""
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


class Calculator:
    """Compute derived metrics from raw PVS6 + Emporia data.

    Supports CORE Electric Cooperative "Residential Demand" (Schedule C) billing:
    - Energy charge + WPCA: $/kWh on net grid kWh
    - Demand charge: $/kW on the highest 60-minute average kW in the billing period
    - Base service charge: flat monthly fee
    - Tax rate: city tax + franchise fee applied to the subtotal
    """

    def __init__(self, energy_rate=0.08907, demand_rate=15.38,
                 base_service_charge=20.00, tax_rate=0.067, wpca_rate=0.0):
        self.energy_rate = energy_rate        # $/kWh
        self.wpca_rate = wpca_rate            # $/kWh wholesale power cost adjustment
        self.demand_rate = demand_rate        # $/kW peak
        self.base_service_charge = base_service_charge  # $/month flat
        self.tax_rate = tax_rate              # decimal (6.7% = 0.067)

    @property
    def marginal_energy_rate(self):
        """$/kWh for one more kWh that does NOT raise the monthly peak (energy + WPCA + taxes)."""
        return (self.energy_rate + self.wpca_rate) * (1 + self.tax_rate)

    @property
    def demand_rate_with_tax(self):
        """$/month for each kW added to the monthly peak, including taxes."""
        return self.demand_rate * (1 + self.tax_rate)

    def compute_realtime(self, pvs6_data, emporia_data):
        """Merge PVS6 and Emporia data into a single reading.

        A source that failed to respond is passed as None; its fields come back
        as None (missing), not 0, so a dead source can't masquerade as 0 W.
        """
        solar_w = house_w = net_grid_w = non_crypto_w = None
        crypto_w = emporia_total_w = None

        if pvs6_data:
            solar_w = pvs6_data.get("production_w", 0) or 0
            house_w = pvs6_data.get("consumption_w", 0) or 0
            # PVS6 is the source of truth for house consumption
            net_grid_w = house_w - solar_w

        if emporia_data:
            # Emporia only monitors the crypto sub-panel, not the whole house
            crypto_w = emporia_data.get("crypto_w", 0) or 0
            emporia_total_w = emporia_data.get("total_w", 0) or 0

        if house_w is not None and crypto_w is not None:
            non_crypto_w = max(house_w - crypto_w, 0)

        def r(v):
            return round(v, 1) if v is not None else None

        return {
            "solar_production_w": r(solar_w),
            "house_consumption_w": r(house_w),
            "net_grid_w": r(net_grid_w),
            "crypto_mining_w": r(crypto_w),
            "non_crypto_house_w": r(non_crypto_w),
            "emporia_total_w": r(emporia_total_w),
            "pvs6_ok": pvs6_data is not None,
            "emporia_ok": emporia_data is not None,
        }

    def compute_energy_cost_per_hour(self, grid_import_w):
        """Marginal energy cost per hour ($/hr) for a grid import, excluding demand."""
        kwh = max(grid_import_w, 0) / 1000
        return round(kwh * self.marginal_energy_rate, 4)

    def compute_demand_cost(self, peak_kw):
        """Demand charge component for billing period (pre-tax, as printed on the bill)."""
        return round(peak_kw * self.demand_rate, 2)

    def compute_monthly_estimate(self, net_grid_kwh, peak_kw):
        """Estimate a full monthly bill.

        net_grid_kwh: projected net grid kWh for the billing period (CORE bills the
            "Net" register = consumption minus export).
        peak_kw: highest 60-minute average grid kW in the period.

        Returns dict with line-item breakdown matching the CORE Electric bill.
        """
        total_kwh = max(net_grid_kwh, 0)

        energy_charge = total_kwh * self.energy_rate
        wpca_charge = total_kwh * self.wpca_rate
        demand_charge = peak_kw * self.demand_rate
        subtotal = self.base_service_charge + energy_charge + wpca_charge + demand_charge
        taxes = subtotal * self.tax_rate
        total = subtotal + taxes

        return {
            "base_service_charge": self.base_service_charge,
            "energy_kwh": round(total_kwh, 1),
            "energy_charge": round(energy_charge, 2),
            "wpca_charge": round(wpca_charge, 2),
            "peak_demand_kw": round(peak_kw, 2),
            "demand_charge": round(demand_charge, 2),
            "subtotal": round(subtotal, 2),
            "taxes": round(taxes, 2),
            "total_estimate": round(total, 2),
            "effective_rate_per_kwh": round(total / total_kwh, 5) if total_kwh > 0 else 0,
            "marginal_energy_rate": round(self.marginal_energy_rate, 5),
            "demand_rate_with_tax": round(self.demand_rate_with_tax, 2),
        }

    def compute_solar_offset(self, solar_w, consumption_w):
        """Calculate solar offset percentage."""
        if consumption_w <= 0:
            return 0
        return round(min((solar_w / consumption_w) * 100, 100), 1)

    def compute_hourly_aggregation(self, readings):
        """Aggregate raw readings into hourly summary values.

        Each field is averaged over the readings where that source responded, so
        an outage in one source doesn't drag its average toward 0.
        """
        if not readings:
            return None

        avg_solar = _avg([r.get("solar_production_w") for r in readings])
        avg_cons = _avg([r.get("house_consumption_w") for r in readings])
        avg_crypto = _avg([r.get("crypto_mining_w") for r in readings])
        avg_grid = _avg([r.get("net_grid_w") for r in readings])
        avg_non_crypto = _avg([r.get("non_crypto_house_w") for r in readings])

        # An hourly average in W equals the hour's energy in Wh
        electricity_cost = None
        solar_savings = None
        if avg_grid is not None:
            # Energy cost for this hour (only grid import), at the marginal rate
            electricity_cost = (max(avg_grid, 0) / 1000) * self.marginal_energy_rate
        if avg_solar is not None and avg_cons is not None:
            # Solar savings = energy charge avoided + demand charge reduction (hourly share).
            # Demand share is a rough proxy: solar_used_kW x demand rate spread over ~720 h.
            solar_used_w = min(avg_solar, avg_cons)
            energy_savings = (solar_used_w / 1000) * self.marginal_energy_rate
            demand_savings = (solar_used_w / 1000) * self.demand_rate_with_tax / 720
            solar_savings = energy_savings + demand_savings

        def r(v, n=1):
            return round(v, n) if v is not None else None

        return {
            "solar_wh": r(avg_solar),
            "house_wh": r(avg_cons),
            "net_grid_wh": r(avg_grid),
            "crypto_wh": r(avg_crypto),
            "non_crypto_wh": r(avg_non_crypto),
            "avg_solar": r(avg_solar),
            "avg_consumption": r(avg_cons),
            "avg_crypto": r(avg_crypto),
            "electricity_cost": r(electricity_cost, 4),
            "solar_savings": r(solar_savings, 4),
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
