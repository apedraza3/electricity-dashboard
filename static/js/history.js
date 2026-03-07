/**
 * History page — date range charts.
 */

let prodVsConsChart = null;
let netGridChart = null;
let cryptoVsHouseChart = null;
let currentRange = 'week';

async function loadRange(range) {
    currentRange = range;

    // Update button states
    document.querySelectorAll('.range-buttons .btn').forEach(b => b.classList.remove('active'));
    const btnId = 'btn' + range.charAt(0).toUpperCase() + range.slice(1);
    const btn = document.getElementById(btnId);
    if (btn) btn.classList.add('active');

    // Calculate date range
    const now = new Date();
    let start;
    switch (range) {
        case 'day':
            start = new Date(now); start.setHours(0, 0, 0, 0);
            break;
        case 'week':
            start = new Date(now.getTime() - 7 * 86400000);
            break;
        case 'month':
            start = new Date(now.getTime() - 30 * 86400000);
            break;
        case 'quarter':
            start = new Date(now.getTime() - 90 * 86400000);
            break;
    }

    const startStr = start.toISOString().split('T')[0];
    const endStr = now.toISOString().split('T')[0];

    // Fetch data — use hourly for day/week, daily for month/quarter
    let url, data;
    if (range === 'day') {
        url = `/api/hourly?start=${startStr}&end=${endStr}`;
    } else {
        url = `/api/daily?start=${startStr}&end=${endStr}`;
    }

    try {
        const resp = await fetch(url);
        data = await resp.json();
    } catch (err) {
        console.error('History fetch error:', err);
        return;
    }

    // Reverse for chronological order
    data.reverse();

    if (data.length === 0) {
        document.getElementById('histCost').textContent = '$0.00';
        document.getElementById('histSavings').textContent = '$0.00';
        document.getElementById('histConsumption').textContent = '0 kWh';
        document.getElementById('histCrypto').textContent = '0 kWh';
        return;
    }

    // Summary cards
    const isHourly = range === 'day';
    const totalCost = data.reduce((s, r) => s + (r.electricity_cost || 0), 0);
    const totalSavings = data.reduce((s, r) => s + (r.solar_savings || 0), 0);
    const totalCons = data.reduce((s, r) => {
        return s + (isHourly ? (r.house_consumption_wh || 0) / 1000 : (r.house_consumption_kwh || 0));
    }, 0);
    const totalCrypto = data.reduce((s, r) => {
        return s + (isHourly ? (r.crypto_mining_wh || 0) / 1000 : (r.crypto_mining_kwh || 0));
    }, 0);

    document.getElementById('histCost').textContent = '$' + totalCost.toFixed(2);
    document.getElementById('histSavings').textContent = '$' + totalSavings.toFixed(2);
    document.getElementById('histConsumption').textContent = totalCons.toFixed(1) + ' kWh';
    document.getElementById('histCrypto').textContent = totalCrypto.toFixed(1) + ' kWh';

    // Labels
    const labels = data.map(r => {
        if (isHourly) {
            return r.hour_start ? r.hour_start.split('T')[1] || r.hour_start : '';
        }
        return r.date || '';
    });

    // Production vs Consumption
    const solarData = data.map(r => isHourly ? (r.solar_production_wh || 0) / 1000 : (r.solar_production_kwh || 0));
    const consData = data.map(r => isHourly ? (r.house_consumption_wh || 0) / 1000 : (r.house_consumption_kwh || 0));

    prodVsConsChart = ElecCharts.line('prodVsConsChart', labels, [
        {
            label: 'Solar (kWh)',
            data: solarData,
            borderColor: ElecCharts.colors.solar,
            backgroundColor: ElecCharts.colors.solar + '20',
            fill: true,
        },
        {
            label: 'Consumption (kWh)',
            data: consData,
            borderColor: ElecCharts.colors.consumption,
            backgroundColor: 'transparent',
        },
    ], prodVsConsChart);

    // Net Grid
    const gridData = data.map(r => isHourly ? (r.net_grid_wh || 0) / 1000 : (r.net_grid_kwh || 0));
    netGridChart = ElecCharts.line('netGridChart', labels, [
        {
            label: 'Net Grid (kWh)',
            data: gridData,
            borderColor: ElecCharts.colors.grid,
            backgroundColor: gridData.map(v => v >= 0 ? 'rgba(239,68,68,0.1)' : 'rgba(34,197,94,0.1)'),
            fill: true,
        },
    ], netGridChart);

    // Crypto vs House
    const cryptoData = data.map(r => isHourly ? (r.crypto_mining_wh || 0) / 1000 : (r.crypto_mining_kwh || 0));
    const nonCryptoData = data.map(r => isHourly ? (r.non_crypto_house_wh || 0) / 1000 : (r.non_crypto_house_kwh || 0));

    cryptoVsHouseChart = ElecCharts.stackedArea('cryptoVsHouseChart', labels, [
        {
            label: 'Crypto Mining (kWh)',
            data: cryptoData,
            borderColor: ElecCharts.colors.crypto,
            backgroundColor: ElecCharts.colors.crypto + '40',
        },
        {
            label: 'Non-Crypto House (kWh)',
            data: nonCryptoData,
            borderColor: ElecCharts.colors.nonCrypto,
            backgroundColor: ElecCharts.colors.nonCrypto + '40',
        },
    ], cryptoVsHouseChart);
}

// Load default range on page load
loadRange('week');
