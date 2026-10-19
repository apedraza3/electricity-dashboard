import logging

import requests

logger = logging.getLogger(__name__)


class PVS6Service:
    """Client for SunPower PVS6 local API via Pi Zero bridge.

    Uses the CGI DeviceList endpoint which returns all devices:
    supervisor, production meter, consumption meter, and inverters.
    """

    DEVICE_LIST_PATH = "/cgi-bin/dl_cgi?Command=DeviceList"

    def __init__(self, host="", port=80, use_https=False, serial="", on_serial=None):
        self.host = host
        self.port = port
        self.use_https = use_https
        self._timeout = 10
        # Firmware 61839+ requires logging in before dl_cgi: HTTP Basic auth as
        # ssm_owner with the last 5 characters of the PVS serial, which sets a
        # session cookie (SunStrong pypvs doc/LocalAPI.md). The serial is learned
        # from DeviceList while it still works unauthenticated, and saved via on_serial.
        self.serial = serial or ""
        self._on_serial = on_serial
        self._session = requests.Session()

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

    @staticmethod
    def _needs_auth(resp) -> bool:
        if resp.status_code in (401, 403):
            return True
        try:
            body = resp.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("status") in (401, 403)

    def _login(self) -> bool:
        if len(self.serial) < 5:
            logger.error("PVS6 requires login but its serial is unknown")
            return False
        resp = self._session.get(self.base_url + "/auth?login", timeout=self._timeout,
                                 verify=False, auth=("ssm_owner", self.serial[-5:]))
        if not resp.ok:
            logger.error("PVS6 login failed: HTTP %s", resp.status_code)
            return False
        logger.info("PVS6 login succeeded")
        return True

    def _learn_serial(self, devices):
        supervisor = next((d for d in devices if d.get("DEVICE_TYPE") == "PVS"), {})
        serial = supervisor.get("SERIAL") or ""
        if serial and serial != self.serial:
            self.serial = serial
            if self._on_serial:
                try:
                    self._on_serial(serial)
                except Exception as e:
                    logger.warning("Could not save PVS6 serial: %s", e)

    def _get_device_list(self):
        """GET DeviceList, logging in once and retrying if the PVS6 demands auth.
        Returns (response, logged_in)."""
        url = self.base_url + self.DEVICE_LIST_PATH
        resp = self._session.get(url, timeout=self._timeout, verify=False)
        if self._needs_auth(resp) and self._login():
            return self._session.get(url, timeout=self._timeout, verify=False), True
        return resp, False

    def is_available(self):
        """Check if PVS6 bridge is reachable."""
        return self._fetch_devices() is not None

    def _fetch_devices(self) -> list[dict] | None:
        """Fetch the full device list from PVS6."""
        if not self.is_configured:
            return None
        try:
            resp, _ = self._get_device_list()
            if resp.ok:
                data = resp.json()
                if data.get("result") == "succeed":
                    devices = data.get("devices", [])
                    self._learn_serial(devices)
                    return devices
            logger.error("PVS6 fetch failed: %s", resp.status_code)
        except Exception as e:
            logger.error("PVS6 fetch error: %s", e)
        return None

    def probe(self) -> dict:
        """One real DeviceList request, for the settings page's Test Connection.

        Returns {"ok": bool, "message": str, "status_code": int|None}.
        """
        if not self.is_configured:
            return {"ok": False, "message": "No PVS6 IP address set", "status_code": None}
        try:
            resp, logged_in = self._get_device_list()
        except requests.RequestException as e:
            return {"ok": False, "message": f"Not reachable: {type(e).__name__}", "status_code": None}
        if self._needs_auth(resp):
            hint = ("serial unknown, so the dashboard can't log in" if len(self.serial) < 5
                    else "login with the serial was rejected")
            return {"ok": False, "status_code": 403,
                    "message": f"403: PVS6 firmware requires login and {hint}"}
        if not resp.ok:
            return {"ok": False, "message": f"HTTP {resp.status_code}", "status_code": resp.status_code}
        try:
            devices = resp.json().get("devices", [])
        except ValueError:
            return {"ok": False, "message": "Reply was not JSON", "status_code": resp.status_code}
        self._learn_serial(devices)
        meters = [d.get("TYPE") for d in devices if d.get("TYPE") in ("PVS5-METER-P", "PVS5-METER-C")]
        supervisor = next((d for d in devices if d.get("DEVICE_TYPE") == "PVS"), {})
        build = supervisor.get("SWVER") or "unknown"
        via = ", logged in" if logged_in else ""
        if len(meters) < 2:
            return {"ok": False, "status_code": resp.status_code,
                    "message": f"Connected{via}, but production/consumption meters missing (firmware {build})"}
        return {"ok": True, "status_code": resp.status_code,
                "message": f"Connected{via}: {len(devices)} devices, both meters (firmware {build})"}

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

        The consumption meter (NET_CONSUMPTION_LOADSIDE) reads net grid flow,
        not total house consumption. Positive = importing, negative = exporting.
        Total house consumption = solar production + net grid reading.

        Returns: {"production_w": float, "consumption_w": float,
                  "production_today_wh": float, "consumption_today_wh": float},
        or None if the PVS6 didn't answer or a meter is missing. Callers must treat
        None as "no data", never as 0 W. (PVS6 firmware build 61839+ returns 403 on
        dl_cgi without a login, which lands here.)
        """
        devices = self._fetch_devices()
        if devices is None:
            return None

        prod_meter = self._find_meter(devices, "PVS5-METER-P")
        cons_meter = self._find_meter(devices, "PVS5-METER-C")
        if not prod_meter or not cons_meter:
            logger.warning("PVS6: production or consumption meter missing from DeviceList")
            return None

        production_w = self._meter_power_w(prod_meter)
        net_grid_w = self._meter_power_w(cons_meter)

        # Total house consumption = solar + net grid import
        # Works in all cases: importing (grid>0), exporting (grid<0), night (solar=0)
        consumption_w = production_w + net_grid_w

        logger.debug(
            "PVS6: production=%.0fW net_grid=%.0fW total_consumption=%.0fW",
            production_w, net_grid_w, consumption_w,
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
