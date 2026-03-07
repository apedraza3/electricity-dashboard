import logging

import requests

logger = logging.getLogger(__name__)

# SunPower Customer Portal API (now SunStrong)
AUTH_URL = "https://monitor.us.sunpower.com/CustomerPortal/Auth/Auth.svc/Authenticate"
CURRENT_POWER_URL = "https://monitor.us.sunpower.com/CustomerPortal/CurrentPower/CurrentPower.svc/GetCurrentPower"
HOURLY_URL = "https://monitor.us.sunpower.com/CustomerPortal/SystemInfo/SystemInfo.svc/getHourlyEnergyData"

# Fallback: SunStrong endpoints
SUNSTRONG_AUTH_URL = "https://monitor.mysunstrong.com/CustomerPortal/Auth/Auth.svc/Authenticate"
SUNSTRONG_POWER_URL = "https://monitor.mysunstrong.com/CustomerPortal/CurrentPower/CurrentPower.svc/GetCurrentPower"


class SunPowerService:
    """Cloud API client for SunPower/SunStrong solar monitoring."""

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self._token = None
        self._auth_url = AUTH_URL
        self._power_url = CURRENT_POWER_URL
        self._timeout = 15

    @property
    def is_configured(self):
        return bool(self.email and self.password)

    def authenticate(self):
        """Login to SunPower/SunStrong cloud API."""
        if not self.is_configured:
            logger.warning("SunPower credentials not configured")
            return False

        # Try multiple credential formats and endpoints
        credential_variants = [
            {"username": self.email, "password": self.password},
            {"userName": self.email, "password": self.password},
            {"email": self.email, "password": self.password},
            {"isPersistent": False, "username": self.email, "password": self.password},
        ]

        auth_endpoints = [
            (AUTH_URL, CURRENT_POWER_URL),
            (SUNSTRONG_AUTH_URL, SUNSTRONG_POWER_URL),
        ]

        for auth_url, power_url in auth_endpoints:
            for credentials in credential_variants:
                try:
                    resp = requests.post(
                        auth_url,
                        json=credentials,
                        timeout=self._timeout,
                        headers={"Content-Type": "application/json"},
                    )
                    logger.info("SunPower auth %s status=%d body=%s",
                                auth_url, resp.status_code, resp.text[:500])

                    if resp.ok:
                        data = resp.json()
                        # Look for token in various response fields
                        token = None
                        for key in ["Guid", "TokenID", "tokenId", "token", "access_token", "StatusCode"]:
                            val = data.get(key)
                            if val and val not in ("Failure", "failure", "error"):
                                token = val
                                break

                        if token:
                            self._token = token
                            self._auth_url = auth_url
                            self._power_url = power_url
                            logger.info("SunPower authenticated via %s", auth_url)
                            return True
                except Exception as e:
                    logger.info("SunPower auth error at %s: %s", auth_url, e)
                    continue

        logger.error("SunPower authentication failed on all endpoints")
        return False

    def ensure_auth(self):
        if not self._token:
            return self.authenticate()
        return True

    def get_current_power(self):
        """Get real-time solar production and house consumption.

        Returns: {"production_w": float, "consumption_w": float} or None
        """
        if not self.ensure_auth():
            return None

        try:
            resp = requests.get(
                self._power_url,
                params={"id": self._token},
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )

            if resp.status_code == 401 or resp.status_code == 403:
                logger.info("SunPower token expired, re-authenticating")
                self._token = None
                if not self.authenticate():
                    return None
                resp = requests.get(
                    self._power_url,
                    params={"id": self._token},
                    timeout=self._timeout,
                    headers={"Content-Type": "application/json"},
                )

            if not resp.ok:
                logger.error("SunPower current power request failed: %d", resp.status_code)
                return None

            data = resp.json()
            return self._parse_current_power(data)

        except Exception as e:
            logger.error("SunPower current power fetch failed: %s", e)
            self._token = None
            return None

    def _parse_current_power(self, data):
        """Parse current power response from various formats."""
        production_w = 0
        consumption_w = 0

        # Format: {"CurrentProduction": X, "CurrentConsumption": Y}
        if "CurrentProduction" in data:
            production_w = float(data.get("CurrentProduction", 0) or 0)
            consumption_w = float(data.get("CurrentConsumption", 0) or 0)

        # Format: direct watts fields
        elif "productionWatts" in data:
            production_w = float(data.get("productionWatts", 0) or 0)
            consumption_w = float(data.get("consumptionWatts", 0) or 0)

        # Format: nested production/consumption objects
        elif "production" in data:
            prod = data.get("production", {})
            cons = data.get("consumption", {})
            if isinstance(prod, dict):
                production_w = float(prod.get("watts", prod.get("wNow", 0)) or 0)
                consumption_w = float(cons.get("watts", cons.get("wNow", 0)) or 0)
            else:
                production_w = float(prod or 0)
                consumption_w = float(cons or 0)

        # Format: list of values [production, consumption]
        elif isinstance(data, list) and len(data) >= 2:
            production_w = float(data[0] or 0)
            consumption_w = float(data[1] or 0)

        # Some APIs return kW, detect and convert
        if 0 < production_w < 100 and consumption_w < 100:
            # Likely kW, convert to W
            production_w *= 1000
            consumption_w *= 1000

        logger.debug("SunPower: production=%.1fW consumption=%.1fW", production_w, consumption_w)

        return {
            "production_w": round(production_w, 1),
            "consumption_w": round(consumption_w, 1),
        }
