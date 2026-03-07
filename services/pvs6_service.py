import logging

import requests
import urllib3

logger = logging.getLogger(__name__)

# Suppress InsecureRequestWarning for self-signed PVS6 certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Known PVS6 endpoint patterns to auto-detect
PRODUCTION_ENDPOINTS = [
    "/api/v1/production",
    "/production.json",
    "/api/v1/production/inverters",
]

CONSUMPTION_ENDPOINTS = [
    "/api/v1/consumption",
    "/consumption.json",
]

HOME_ENDPOINTS = [
    "/api/v1/home",
    "/home.json",
]


class PVS6Service:
    """Client for SunPower PVS6 local REST API."""

    def __init__(self, host="", port=443, use_https=True):
        self.host = host
        self.port = port
        self.use_https = use_https
        self._production_endpoint = None
        self._consumption_endpoint = None
        self._home_endpoint = None
        self._timeout = 5

    @property
    def base_url(self):
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def is_configured(self):
        return bool(self.host)

    def update_config(self, host, port, use_https):
        """Update connection settings and reset cached endpoints."""
        self.host = host
        self.port = port
        self.use_https = use_https
        self._production_endpoint = None
        self._consumption_endpoint = None
        self._home_endpoint = None

    def is_available(self):
        """Check if PVS6 is reachable."""
        if not self.is_configured:
            return False
        try:
            resp = requests.get(
                self.base_url + "/",
                timeout=self._timeout,
                verify=False,
            )
            return resp.status_code < 500
        except requests.exceptions.RequestException:
            return False

    def _detect_endpoint(self, candidates):
        """Try candidate endpoints and return the first that works."""
        for endpoint in candidates:
            url = self.base_url + endpoint
            try:
                resp = requests.get(url, timeout=self._timeout, verify=False)
                if resp.ok:
                    logger.info("PVS6 endpoint detected: %s", endpoint)
                    return endpoint
            except requests.exceptions.RequestException:
                continue
        return None

    def _fetch(self, endpoint):
        """GET an endpoint and return parsed JSON or None."""
        if not self.is_configured:
            return None
        try:
            resp = requests.get(
                self.base_url + endpoint,
                timeout=self._timeout,
                verify=False,
            )
            if resp.ok:
                return resp.json()
        except Exception as e:
            logger.error("PVS6 fetch failed (%s): %s", endpoint, e)
        return None

    def get_production(self):
        """Get solar production data.

        Returns: {"power_w": float, "energy_today_wh": float} or None
        """
        if not self.is_configured:
            return None

        if self._production_endpoint is None:
            self._production_endpoint = self._detect_endpoint(PRODUCTION_ENDPOINTS)
        if self._production_endpoint is None:
            return None

        data = self._fetch(self._production_endpoint)
        if data is None:
            return None

        return self._parse_production(data)

    def get_consumption(self):
        """Get house consumption data.

        Returns: {"power_w": float, "energy_today_wh": float} or None
        """
        if not self.is_configured:
            return None

        if self._consumption_endpoint is None:
            self._consumption_endpoint = self._detect_endpoint(CONSUMPTION_ENDPOINTS)
        if self._consumption_endpoint is None:
            # Try home endpoint as fallback (some PVS6 units combine prod+cons)
            return self._get_consumption_from_home()

        data = self._fetch(self._consumption_endpoint)
        if data is None:
            return None

        return self._parse_consumption(data)

    def _get_consumption_from_home(self):
        """Fallback: get consumption from /home endpoint."""
        if self._home_endpoint is None:
            self._home_endpoint = self._detect_endpoint(HOME_ENDPOINTS)
        if self._home_endpoint is None:
            return None

        data = self._fetch(self._home_endpoint)
        if data is None:
            return None

        return self._parse_consumption(data)

    def get_both(self):
        """Get both production and consumption in one call.

        Returns: {"production_w": float, "consumption_w": float,
                  "production_today_wh": float, "consumption_today_wh": float}
        """
        prod = self.get_production()
        cons = self.get_consumption()

        return {
            "production_w": prod["power_w"] if prod else 0,
            "consumption_w": cons["power_w"] if cons else 0,
            "production_today_wh": prod.get("energy_today_wh", 0) if prod else 0,
            "consumption_today_wh": cons.get("energy_today_wh", 0) if cons else 0,
        }

    def _parse_production(self, data):
        """Parse production data from various PVS6 response formats."""
        # Format 1: {"production": [{"type": "inverters", "activeCount": N,
        #            "readingTime": T, "wNow": W, "whLifetime": WH, "whToday": WH}]}
        if isinstance(data, dict) and "production" in data:
            for entry in data["production"]:
                if entry.get("type") in ("inverters", "eim"):
                    return {
                        "power_w": entry.get("wNow", 0),
                        "energy_today_wh": entry.get("whToday", 0),
                    }

        # Format 2: list of inverter objects
        if isinstance(data, list):
            total_w = sum(inv.get("wNow", inv.get("lastReportWatts", 0)) for inv in data)
            return {"power_w": total_w, "energy_today_wh": 0}

        # Format 3: flat object
        if isinstance(data, dict):
            return {
                "power_w": data.get("wNow", data.get("wattsNow", data.get("power", 0))),
                "energy_today_wh": data.get("whToday", data.get("wattHoursToday", 0)),
            }

        return None

    def _parse_consumption(self, data):
        """Parse consumption data from various PVS6 response formats."""
        # Format 1: {"consumption": [{"type": "eim", "wNow": W, ...}]}
        if isinstance(data, dict) and "consumption" in data:
            for entry in data["consumption"]:
                if entry.get("measurementType") == "total-consumption" or entry.get("type") == "eim":
                    return {
                        "power_w": entry.get("wNow", 0),
                        "energy_today_wh": entry.get("whToday", 0),
                    }
            # Take first entry if no specific type found
            if data["consumption"]:
                entry = data["consumption"][0]
                return {
                    "power_w": entry.get("wNow", 0),
                    "energy_today_wh": entry.get("whToday", 0),
                }

        # Format 2: flat object (from /home endpoint)
        if isinstance(data, dict):
            return {
                "power_w": data.get("wNow", data.get("wattsNow", data.get("consumption", 0))),
                "energy_today_wh": data.get("whToday", data.get("wattHoursToday", 0)),
            }

        return None
