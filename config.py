import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# Paths
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "electricity.db"

# PVS6 (SunPower solar inverter on LAN)
PVS6_HOST = os.getenv("PVS6_HOST", "")
PVS6_PORT = int(os.getenv("PVS6_PORT", "443"))
PVS6_USE_HTTPS = os.getenv("PVS6_USE_HTTPS", "true").lower() == "true"

# Emporia Energy Vue (cloud API)
EMPORIA_EMAIL = os.getenv("EMPORIA_EMAIL", "")
EMPORIA_PASSWORD = os.getenv("EMPORIA_PASSWORD", "")

# Electricity rate
ELECTRICITY_RATE = float(os.getenv("ELECTRICITY_RATE", "0.12"))

# Crypto circuit IDs (comma-separated Emporia channel numbers)
CRYPTO_CIRCUIT_IDS = [
    int(x.strip()) for x in os.getenv("CRYPTO_CIRCUIT_IDS", "").split(",")
    if x.strip()
]

# Polling
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
HISTORY_POLL_INTERVAL = int(os.getenv("HISTORY_POLL_INTERVAL", "900"))

# Network discovery
DISCOVERY_SUBNET = os.getenv("DISCOVERY_SUBNET", "192.168.1.0/24")

# Data retention (days)
RAW_RETENTION_DAYS = int(os.getenv("RAW_RETENTION_DAYS", "7"))
HOURLY_RETENTION_DAYS = int(os.getenv("HOURLY_RETENTION_DAYS", "90"))

# Flask
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"
