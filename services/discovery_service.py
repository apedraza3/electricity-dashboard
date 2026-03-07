import socket
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress

import requests

logger = logging.getLogger(__name__)

# Known PVS6 API endpoints to probe
PVS6_ENDPOINTS = [
    "/api/v1/production",
    "/production.json",
    "/api/v1/home",
    "/cgi-bin/dl_cgi?Command=DeviceList",
]


def discover_pvs6(subnet="192.168.1.0/24"):
    """Scan a subnet for SunPower PVS6 devices.

    Returns list of dicts: [{"ip", "port", "https", "model"}]
    """
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        logger.error("Invalid subnet: %s", subnet)
        return []

    hosts = [str(ip) for ip in network.hosts()]
    results = []

    # Phase 1: TCP connect scan on ports 80 and 443
    open_hosts = []
    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = {}
        for host in hosts:
            for port in [80, 443]:
                f = pool.submit(_tcp_check, host, port)
                futures[f] = (host, port)

        for f in as_completed(futures, timeout=60):
            host, port = futures[f]
            try:
                if f.result():
                    open_hosts.append((host, port))
            except Exception:
                pass

    logger.info("Found %d open host:port combinations", len(open_hosts))

    # Phase 2: Probe PVS6 endpoints on open hosts
    for host, port in open_hosts:
        use_https = port == 443
        result = _probe_pvs6(host, port, use_https)
        if result:
            results.append(result)

    return results


def _tcp_check(host, port, timeout=2):
    """Check if a TCP port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _probe_pvs6(host, port, use_https):
    """Try known PVS6 endpoints to confirm it's a PVS6 device."""
    scheme = "https" if use_https else "http"
    base = f"{scheme}://{host}:{port}"

    for endpoint in PVS6_ENDPOINTS:
        url = base + endpoint
        try:
            resp = requests.get(url, timeout=5, verify=False)
            if resp.ok:
                # Must be JSON — skip HTML responses (routers, etc.)
                content_type = resp.headers.get("Content-Type", "")
                if "html" in content_type.lower():
                    continue
                try:
                    data = resp.json()
                except Exception:
                    continue

                model = "PVS6"
                if isinstance(data, dict):
                    model = data.get("type", data.get("model", "PVS6"))
                elif isinstance(data, list) and len(data) > 0:
                    model = data[0].get("type", "PVS6")

                logger.info("Found PVS6 at %s (endpoint: %s)", base, endpoint)
                return {
                    "ip": host,
                    "port": port,
                    "https": use_https,
                    "model": model,
                    "endpoint": endpoint,
                }
        except requests.exceptions.RequestException:
            continue

    return None
