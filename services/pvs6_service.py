import logging

import requests

logger = logging.getLogger(__name__)


class PVS6Service:
    """Client for SunPower PVS6 local API via Pi Zero bridge.

    Uses the CGI DeviceList endpoint which returns all devices:
    supervisor, production meter, consumption meter, and inverters.
    """

    DEVICE_LIST_PATH = "/cgi-bin/dl_cgi?Command=DeviceList"

    def __init__(self, host="", port=80, use_https=False):
        self.host = host
        self.port = port
        self.use_https = use_https
        self._timeout = 10

    @property
    def base_url(self):
        scheme = "https" if self.use_https else "http"
        if (not self.use_https and self.port == 80) or (self.use_https and self.port == 443):
            return f"{scheme}://{self.host}"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def is_configured(self):
        return bool(self.host)

    def update_config(self, host, port, use_https):
        """Update connection settings."""
        self.host = host
        self.port = port
        self.use_https = use_https

    def is_available(self):
        """Check if PVS6 bridge is reachable."""
        if not self.is_configured:
            return False
        try:
            resp = requests.get(
                self.base_url + self.DEVICE_LIST_PATH,
                timeout=self._timeout,
                verify=False,
            )
            return resp.ok and resp.json().get("result") == "succeed"
        except Exception:
            return False

    def _fetch_devices(self) -> list[dict] | None:
        """Fetch the full device list from PVS6."""
        if not self.is_configured:
            return None
        try:
            resp = requests.get(
                self.base_url + self.DEVICE_LIST_PATH,
                timeout=self._timeout,
                verify=False,
            )
            if resp.ok:
                data = resp.json()
                if data.get("result") == "succeed":
                    return data.get("devices", [])
            logger.error("PVS6 fetch failed: %s", resp.status_code)
        except Exception as e:
            logger.error("PVS6 fetch error: %s", e)
        return None

    def _find_meter(self, devices: list[dict], meter_type: str) -> dict | None:
        """Find a meter device by TYPE (PVS5-METER-P or PVS5-METER-C)."""
        for dev in devices:
            if dev.get("TYPE") == meter_type:
                return dev
        return None

    def _meter_power_w(self, meter: dict | None) -> float:
        """Extract current power in watts from a meter device."""
        if not meter:
            return 0
        try:
            return float(meter.get("p_3phsum_kw", 0)) * 1000
        except (ValueError, TypeError):
            return 0

    def _meter_lifetime_kwh(self, meter: dict | None) -> float:
        """Extract lifetime energy in kWh from a meter device."""
        if not meter:
            return 0
        try:
            return float(meter.get("net_ltea_3phsum_kwh", 0))
        except (ValueError, TypeError):
            return 0

    def get_production(self):
        """Get solar production data.

        Returns: {"power_w": float, "energy_today_wh": float} or None
        """
        devices = self._fetch_devices()
        if devices is None:
            return None

        meter = self._find_meter(devices, "PVS5-METER-P")
        if not meter:
            logger.warning("PVS6: no production meter found")
            return None

        power_w = self._meter_power_w(meter)
        return {"power_w": power_w, "energy_today_wh": 0}

    def get_consumption(self):
        """Get house consumption data.

        Returns: {"power_w": float, "energy_today_wh": float} or None
        """
        devices = self._fetch_devices()
        if devices is None:
            return None

        meter = self._find_meter(devices, "PVS5-METER-C")
        if not meter:
            logger.warning("PVS6: no consumption meter found")
            return None

        power_w = self._meter_power_w(meter)
        return {"power_w": power_w, "energy_today_wh": 0}

    def get_both(self):
        """Get both production and consumption in one API call.

        Returns: {"production_w": float, "consumption_w": float,
                  "production_today_wh": float, "consumption_today_wh": float}
        """
        devices = self._fetch_devices()
        if devices is None:
            return {
                "production_w": 0,
                "consumption_w": 0,
                "production_today_wh": 0,
                "consumption_today_wh": 0,
            }

        prod_meter = self._find_meter(devices, "PVS5-METER-P")
        cons_meter = self._find_meter(devices, "PVS5-METER-C")

        production_w = self._meter_power_w(prod_meter)
        consumption_w = self._meter_power_w(cons_meter)

        logger.debug(
            "PVS6: production=%.0fW consumption=%.0fW",
            production_w, consumption_w,
        )

        return {
            "production_w": production_w,
            "consumption_w": consumption_w,
            "production_today_wh": 0,
            "consumption_today_wh": 0,
        }

    def get_inverters(self) -> list[dict]:
        """Get per-inverter data for detailed monitoring.

        Returns list of: {"serial": str, "panel": str, "power_w": float,
                          "lifetime_kwh": float, "voltage_v": float, "temp_c": float,
                          "state": str}
        """
        devices = self._fetch_devices()
        if devices is None:
            return []

        inverters = []
        for dev in devices:
            if dev.get("TYPE") != "SOLARBRIDGE":
                continue
            try:
                inverters.append({
                    "serial": dev.get("SERIAL", ""),
                    "panel": dev.get("PANEL", ""),
                    "power_w": float(dev.get("p_3phsum_kw", 0)) * 1000,
                    "lifetime_kwh": float(dev.get("ltea_3phsum_kwh", 0)),
                    "voltage_v": float(dev.get("vln_3phavg_v", 0)),
                    "temp_c": float(dev.get("t_htsnk_degc", 0)),
                    "state": dev.get("STATE", "unknown"),
                })
            except (ValueError, TypeError):
                continue

        return inverters

    def get_system_info(self) -> dict | None:
        """Get PVS6 supervisor info.

        Returns: {"serial": str, "model": str, "firmware": str, "uptime_min": int} or None
        """
        devices = self._fetch_devices()
        if devices is None:
            return None

        for dev in devices:
            if dev.get("DEVICE_TYPE") == "PVS":
                return {
                    "serial": dev.get("SERIAL", ""),
                    "model": dev.get("MODEL", ""),
                    "firmware": dev.get("SWVER", ""),
                    "uptime_min": int(dev.get("dl_uptime", 0)),
                }
        return None
