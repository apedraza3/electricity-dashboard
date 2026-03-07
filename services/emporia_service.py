import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class EmporiaService:
    """Wrapper around pyemvue for Emporia Energy Vue data."""

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.vue = None
        self.devices = None
        self._authenticated = False

    def authenticate(self):
        """Login to Emporia cloud API."""
        if not self.email or not self.password:
            logger.warning("Emporia credentials not configured")
            return False

        try:
            from pyemvue import PyEmVue
            self.vue = PyEmVue()
            self.vue.login(username=self.email, password=self.password)
            self._authenticated = True
            self.devices = self.vue.get_devices()
            logger.info("Emporia authenticated. Found %d device(s).", len(self.devices))
            return True
        except Exception as e:
            logger.error("Emporia authentication failed: %s", e)
            self._authenticated = False
            return False

    @property
    def is_authenticated(self):
        return self._authenticated and self.vue is not None

    def ensure_auth(self):
        """Re-authenticate if needed."""
        if not self.is_authenticated:
            return self.authenticate()
        return True

    def get_devices(self):
        """Return list of device info dicts."""
        if not self.ensure_auth():
            return []
        return [
            {
                "device_gid": d.device_gid,
                "model": getattr(d, "model", "Vue"),
                "firmware": getattr(d, "firmware", ""),
                "channels": [
                    {
                        "channel_num": ch.channel_num,
                        "name": ch.name or f"Channel {ch.channel_num}",
                        "channel_multiplier": getattr(ch, "channel_multiplier", 1.0),
                    }
                    for ch in (d.channels or [])
                ],
            }
            for d in (self.devices or [])
        ]

    def get_instant_usage(self, crypto_circuit_ids=None):
        """Get real-time usage from Emporia Vue monitoring the crypto sub-panel.

        The Vue monitors the entire sub-panel mains (combined '1,2,3' channel).
        All power on this sub-panel is crypto mining power.

        Returns dict with:
            - total_w: total watts on the sub-panel
            - crypto_w: same as total_w (entire sub-panel is crypto)
            - circuits: list with one entry for the sub-panel mains
        """
        if not self.ensure_auth():
            return {"total_w": 0, "crypto_w": 0, "non_crypto_w": 0, "circuits": []}

        try:
            from pyemvue.enums import Scale, Unit

            device_gids = list(set(d.device_gid for d in self.devices))
            usage_dict = self.vue.get_device_list_usage(
                deviceGids=device_gids,
                instant=datetime.utcnow(),
                scale=Scale.SECOND.value,
                unit=Unit.KWH.value,
            )

            crypto_w = 0
            for gid, device_usage in usage_dict.items():
                if not hasattr(device_usage, "channels"):
                    continue
                for ch_num, channel in device_usage.channels.items():
                    usage_kwh_per_sec = channel.usage or 0
                    # kWh/s → watts = kWh/s * 3600 * 1000
                    crypto_w += usage_kwh_per_sec * 3600 * 1000

            crypto_w = round(crypto_w, 1)
            logger.debug("Emporia sub-panel: %.1fW", crypto_w)

            circuits = [{
                "channel_num": 0,
                "name": "Crypto Sub-Panel",
                "usage_w": crypto_w,
                "is_crypto": True,
                "device_gid": device_gids[0] if device_gids else None,
            }]

            return {
                "total_w": crypto_w,
                "crypto_w": crypto_w,
                "non_crypto_w": 0,
                "circuits": circuits,
            }

        except Exception as e:
            logger.error("Emporia usage fetch failed: %s", e)
            # Try re-auth on next call
            self._authenticated = False
            return {"total_w": 0, "crypto_w": 0, "non_crypto_w": 0, "circuits": []}

    def get_usage_for_range(self, start, end, scale="DAY", unit="KWH"):
        """Get historical usage for a date range.

        Returns list of usage data points.
        """
        if not self.ensure_auth():
            return []

        try:
            from pyemvue.enums import Scale, Unit as EmUnit

            scale_val = getattr(Scale, scale).value
            unit_val = getattr(EmUnit, unit).value

            results = []
            for device in self.devices:
                for channel in (device.channels or []):
                    chart_usage = self.vue.get_chart_usage(
                        channel,
                        start,
                        end,
                        scale=scale_val,
                        unit=unit_val,
                    )
                    if chart_usage:
                        results.append({
                            "device_gid": device.device_gid,
                            "channel_num": channel.channel_num,
                            "channel_name": channel.name,
                            "usage": chart_usage,
                        })
            return results

        except Exception as e:
            logger.error("Emporia historical fetch failed: %s", e)
            return []
