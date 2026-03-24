# Electricity Dashboard

Real-time energy monitoring platform that fuses data from solar inverters and smart meters to track power production, consumption, grid flow, and crypto mining separately. Calculates electricity costs using a tiered utility billing model, tracks solar savings, and estimates monthly bills with demand charge awareness.

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask)
![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

## How It Works

```
Hardware Sensors → Data Collector → SQLite → Aggregation Pipeline → Dashboard + REST API
```

1. **Collect** — Background thread polls a SunPower PVS6 inverter (local LAN) and Emporia Vue smart meter (cloud API) every 60 seconds
2. **Store** — Raw readings saved to SQLite with per-circuit breakdowns
3. **Aggregate** — Hourly rollups every 15 minutes, daily rollups every hour, automatic data retention purge
4. **Serve** — Real-time cached readings (O(1) response), historical queries, bill estimates, solar ROI calculations

## Hardware Integrations

| Device | Connection | What It Provides |
|--------|-----------|-----------------|
| **SunPower PVS6** | Local HTTP API via Raspberry Pi Zero bridge | Solar production (W), net grid flow (W), per-inverter stats |
| **Emporia Energy Vue** | Cloud REST API (pyemvue) | Per-circuit power usage, crypto mining circuit identification |

The dashboard separates crypto mining power from household consumption by tagging specific Emporia circuits as mining loads.

## Features

- **Real-Time Dashboard** — Live power readings with 10-second auto-refresh: solar production, house consumption, net grid, crypto mining
- **Circuit Breakdown** — Per-circuit power usage from Emporia Vue with crypto/non-crypto tagging
- **Historical Analysis** — Stacked area charts for production vs. consumption, net grid flow, and crypto vs. house usage over 1–90 day ranges
- **Bill Estimation** — Monthly forecast using tiered utility billing (energy charge + demand charge + base fee + tax)
- **Solar ROI Tracker** — System cost, incentives, total production, cumulative savings, payback percentage, months to breakeven, annual ROI
- **Demand Charge Tracking** — Tracks 30-day rolling peak grid demand (kW), which drives the most expensive part of commercial/co-op electric bills
- **PVS6 Discovery** — Multi-threaded network scanner to find SunPower inverters on the LAN
- **Runtime Configuration** — Update rates, polling intervals, and device settings via the UI without restarting

## Billing Model

Models a tiered cooperative utility structure (CORE Electric):

```
Energy Charge  = consumption_kwh × $0.089/kWh
Demand Charge  = peak_grid_kw × $15.38/kW      ← biggest cost driver
Base Charge    = $20.00/month
Tax            = subtotal × 6.72%
```

Solar savings are calculated as avoided grid import — only solar production that directly offsets consumption counts, not excess exported to the grid.

## Architecture

```
electricity-dashboard/
├── app.py                        # Flask routes + 22 API endpoints
├── config.py                     # Environment configuration
├── backfill.py                   # One-time historical data aggregation
├── services/
│   ├── db.py                     # SQLite schema, CRUD, WAL mode, retention
│   ├── collector.py              # Background polling thread + aggregation
│   ├── calculator.py             # Bill estimation, solar savings, demand tracking
│   ├── pvs6_service.py           # SunPower PVS6 local API client + parser
│   ├── emporia_service.py        # Emporia Vue cloud API client (pyemvue)
│   └── discovery_service.py      # Multi-threaded network scanner for PVS6
├── templates/                    # Jinja2 pages (dashboard, circuits, history, solar, settings)
└── static/
    ├── css/dashboard.css         # Dark theme UI
    └── js/                       # Chart.js visualizations + live updates
```

## Data Pipeline

Three-tier aggregation with automatic retention:

| Tier | Granularity | Retention | Purpose |
|------|------------|-----------|---------|
| **Raw readings** | Every 60s | 7 days | Real-time display, per-circuit data |
| **Hourly summary** | 1 hour | 90 days | Historical charts, cost calculations |
| **Daily summary** | 1 day | Forever | Long-term trends, bill estimates, solar ROI |

The collector thread handles aggregation inline — no external scheduler or job queue needed.

## API

22 endpoints covering real-time data, historical queries, billing, and solar ROI:

| Endpoint | Description |
|----------|------------|
| `GET /api/realtime` | Current power readings (cached, no DB hit) |
| `GET /api/circuits` | Per-circuit breakdown with crypto flags |
| `GET /api/hourly` | Hourly summaries for date range |
| `GET /api/daily` | Daily summaries for date range |
| `GET /api/costs?period=month` | Cost breakdown for period |
| `GET /api/bill-estimate` | Monthly bill forecast with demand charge |
| `GET /api/solar/roi` | Payback %, months to breakeven, annual ROI |
| `PUT /api/settings` | Update config at runtime (no restart) |
| `POST /api/discovery/scan` | Scan LAN for PVS6 devices |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.13, Flask 3.1 |
| **Database** | SQLite with WAL mode (concurrent reads + writes) |
| **Hardware** | SunPower PVS6 (local HTTP), Emporia Vue (cloud API via pyemvue) |
| **Frontend** | Vanilla JS, Chart.js 4.4, Jinja2 templates |
| **Auth** | Optional bcrypt password hashing + Flask sessions |
| **Deployment** | Docker Compose with host networking (LAN access for PVS6) |

## Setup

### Prerequisites
- Docker and Docker Compose
- SunPower PVS6 inverter accessible on LAN (via Pi Zero bridge or direct)
- Emporia Energy Vue account (optional — for per-circuit crypto tracking)

### Quick Start

```bash
git clone https://github.com/apedraza3/electricity-dashboard.git
cd electricity-dashboard
cp .env.example .env
# Edit .env with your device IPs, Emporia credentials, and utility rates
docker compose up -d
```

The dashboard runs at `http://localhost:5001`.

### Key Configuration

```env
# Hardware
PVS6_HOST=192.168.x.x          # Pi Zero bridge IP
EMPORIA_EMAIL=your@email.com    # Emporia Vue login
EMPORIA_PASSWORD=your_password

# Utility Rates (match your electric bill)
ENERGY_RATE=0.08907             # $/kWh
DEMAND_RATE=15.38               # $/kW peak demand
BASE_SERVICE_CHARGE=20.00       # $/month
TAX_RATE=0.0672                 # 6.72%

# Mining Circuits
CRYPTO_CIRCUIT_IDS=4,5,6        # Emporia channel numbers for mining loads
```

All settings can also be updated at runtime through the Settings page — changes take effect on the next poll cycle without restarting the container.
