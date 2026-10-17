/**
 * Main dashboard page logic.
 * Auto-refreshes realtime data every 10 seconds.
 */

let liveChart = null;
let todayChart = null;
let liveData = { labels: [], solar: [], consumption: [], crypto: [], grid: [] };
let refreshInterval = null;

// Max points on live chart (1 hour at 10s intervals = 360)
const MAX_LIVE_POINTS = 360;

async function fetchRealtime() {
    try {
        const resp = await fetch('/api/realtime');
        const data = await resp.json();

        if (data.status === 'no_data') {
            document.getElementById('statusDot').className = 'status-dot warn';
            document.getElementById('statusText').textContent = 'No data yet';
            return;
        }

        // Update hero cards
        const solarW = data.solar_production_w || 0;
        const consW = data.house_consumption_w || 0;
        const gridW = data.net_grid_w || 0;
        const cryptoW = data.crypto_mining_w || 0;
        const nonCryptoW = data.non_crypto_house_w || 0;

        const solarEl = document.getElementById('solarValue');
        solarEl.textContent = ElecCharts.formatKw(solarW) + ' kW';
        solarEl.className = 'card-value val-solar';

        const consEl = document.getElementById('consumptionValue');
        consEl.textContent = ElecCharts.formatKw(consW) + ' kW';
        consEl.className = 'card-value val-consumption';

        const gridEl = document.getElementById('gridValue');
        gridEl.textContent = ElecCharts.formatKw(gridW) + ' kW';
        gridEl.className = 'card-value ' + (gridW >= 0 ? 'val-grid-import' : 'val-grid-export');
        document.getElementById('gridSub').textContent = gridW >= 0 ? 'Importing from grid' : 'Exporting to grid';

        const cryptoEl = document.getElementById('cryptoValue');
        cryptoEl.textContent = ElecCharts.formatKw(cryptoW) + ' kW';
        cryptoEl.className = 'card-value val-crypto';

        // Metrics row
        document.getElementById('nonCryptoValue').textContent = ElecCharts.formatKw(nonCryptoW) + ' kW';

        // Cost per hour: grid import kW * marginal energy rate (energy + WPCA + taxes).
        // Solar-covered load and exports don't cost energy charges; demand is billed separately.
        const rates = await getRates();
        const marginalRate = (rates.energy_rate + rates.wpca_rate) * (1 + rates.tax_rate);
        const costPerHr = (Math.max(gridW, 0) / 1000) * marginalRate;
        document.getElementById('costHrValue').textContent = '$' + costPerHr.toFixed(2);

        // Solar offset %
        const offset = consW > 0 ? Math.min((solarW / consW) * 100, 100) : 0;
        document.getElementById('solarOffsetValue').textContent = offset.toFixed(0) + '%';

        // Update live chart data
        const timeLabel = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        liveData.labels.push(timeLabel);
        liveData.solar.push(solarW / 1000);
        liveData.consumption.push(consW / 1000);
        liveData.crypto.push(cryptoW / 1000);
        liveData.grid.push(gridW / 1000);

        // Trim to max points
        if (liveData.labels.length > MAX_LIVE_POINTS) {
            liveData.labels.shift();
            liveData.solar.shift();
            liveData.consumption.shift();
            liveData.crypto.shift();
            liveData.grid.shift();
        }

        renderLiveChart();

        // Status
        document.getElementById('statusDot').className = 'status-dot ok';
        document.getElementById('statusText').textContent = 'Connected';
        document.getElementById('lastUpdated').textContent = 'Last: ' + (data.timestamp || timeLabel);

    } catch (err) {
        console.error('Realtime fetch error:', err);
        document.getElementById('statusDot').className = 'status-dot error';
        document.getElementById('statusText').textContent = 'Connection error';
    }
}

async function fetchTodaySummary() {
    try {
        const resp = await fetch('/api/summary');
        const data = await resp.json();

        document.getElementById('todayKwhValue').textContent =
            (data.consumption_kwh || 0).toFixed(1) + ' kWh';

        // Today bar chart
        const labels = ['Solar', 'Consumption', 'Crypto', 'Non-Crypto', 'Net Grid'];
        const values = [
            data.solar_kwh || 0,
            data.consumption_kwh || 0,
            data.crypto_kwh || 0,
            data.non_crypto_kwh || 0,
            data.net_grid_kwh || 0,
        ];
        const colors = [
            ElecCharts.colors.solar,
            ElecCharts.colors.consumption,
            ElecCharts.colors.crypto,
            ElecCharts.colors.nonCrypto,
            ElecCharts.colors.grid,
        ];
        todayChart = ElecCharts.bar('todayChart', labels, values, colors, todayChart);
    } catch (err) {
        console.error('Summary fetch error:', err);
    }
}

function renderLiveChart() {
    liveChart = ElecCharts.line('liveChart', liveData.labels, [
        {
            label: 'Solar (kW)',
            data: liveData.solar,
            borderColor: ElecCharts.colors.solar,
            backgroundColor: ElecCharts.colors.solar + '20',
            fill: true,
        },
        {
            label: 'Consumption (kW)',
            data: liveData.consumption,
            borderColor: ElecCharts.colors.consumption,
            backgroundColor: 'transparent',
        },
        {
            label: 'Crypto (kW)',
            data: liveData.crypto,
            borderColor: ElecCharts.colors.crypto,
            backgroundColor: 'transparent',
        },
        {
            label: 'Net Grid (kW)',
            data: liveData.grid,
            borderColor: ElecCharts.colors.grid,
            backgroundColor: 'transparent',
            borderDash: [5, 5],
        },
    ], liveChart);
}

let cachedRates = null;
async function getRates() {
    if (cachedRates !== null) return cachedRates;
    try {
        const resp = await fetch('/api/settings');
        const data = await resp.json();
        cachedRates = {
            energy_rate: data.energy_rate || 0.08907,
            wpca_rate: data.wpca_rate ?? 0.00379,
            demand_rate: data.demand_rate || 15.38,
            base_service_charge: data.base_service_charge || 20.00,
            tax_rate: data.tax_rate || 0.067,
        };
        return cachedRates;
    } catch {
        return { energy_rate: 0.08907, wpca_rate: 0.00379, demand_rate: 15.38, base_service_charge: 20, tax_rate: 0.067 };
    }
}

function startAutoRefresh() {
    fetchRealtime();
    fetchTodaySummary();
    refreshInterval = setInterval(() => {
        fetchRealtime();
        fetchTodaySummary();
    }, 10000);
}

function stopAutoRefresh() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = null;
    }
}

// Only run on dashboard page
if (document.getElementById('liveChart')) {
    startAutoRefresh();

    document.getElementById('autoRefresh').addEventListener('change', function () {
        if (this.checked) {
            startAutoRefresh();
        } else {
            stopAutoRefresh();
        }
    });
}
