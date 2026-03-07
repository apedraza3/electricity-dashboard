/**
 * Reusable Chart.js helpers for the electricity dashboard.
 * Consistent dark theme colors matching dashboard.css.
 */
const ElecCharts = {
    colors: {
        solar: '#22c55e',
        consumption: '#ef4444',
        grid: '#3b82f6',
        crypto: '#f59e0b',
        nonCrypto: '#8b5cf6',
        gridLine: '#334155',
        text: '#94a3b8',
    },

    defaultOptions: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                labels: { color: '#94a3b8', font: { size: 12 } },
            },
        },
        scales: {
            x: {
                ticks: { color: '#94a3b8', font: { size: 11 } },
                grid: { color: '#334155' },
            },
            y: {
                ticks: { color: '#94a3b8', font: { size: 11 } },
                grid: { color: '#334155' },
            },
        },
    },

    /**
     * Create or update a line chart.
     */
    line(canvasId, labels, datasets, existingChart, options) {
        if (existingChart) existingChart.destroy();
        const ctx = document.getElementById(canvasId).getContext('2d');
        return new Chart(ctx, {
            type: 'line',
            data: { labels, datasets },
            options: {
                ...this.defaultOptions,
                ...options,
                interaction: { mode: 'index', intersect: false },
                elements: { point: { radius: 0 }, line: { tension: 0.3 } },
            },
        });
    },

    /**
     * Create or update a bar chart.
     */
    bar(canvasId, labels, values, colors, existingChart, options) {
        if (existingChart) existingChart.destroy();
        const ctx = document.getElementById(canvasId).getContext('2d');
        return new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    data: values,
                    backgroundColor: Array.isArray(colors) ? colors : [colors],
                    borderRadius: 4,
                }],
            },
            options: {
                ...this.defaultOptions,
                ...options,
                plugins: {
                    ...this.defaultOptions.plugins,
                    legend: { display: false },
                },
            },
        });
    },

    /**
     * Create or update a stacked area chart.
     */
    stackedArea(canvasId, labels, datasets, existingChart, options) {
        if (existingChart) existingChart.destroy();
        const ctx = document.getElementById(canvasId).getContext('2d');
        return new Chart(ctx, {
            type: 'line',
            data: { labels, datasets: datasets.map(ds => ({ ...ds, fill: true })) },
            options: {
                ...this.defaultOptions,
                ...options,
                interaction: { mode: 'index', intersect: false },
                elements: { point: { radius: 0 }, line: { tension: 0.3 } },
                scales: {
                    ...this.defaultOptions.scales,
                    y: { ...this.defaultOptions.scales.y, stacked: true },
                    x: { ...this.defaultOptions.scales.x, stacked: true },
                },
            },
        });
    },

    /**
     * Format watts to human-readable.
     */
    formatWatts(w) {
        if (w === null || w === undefined) return '--';
        if (Math.abs(w) >= 1000) return (w / 1000).toFixed(2) + ' kW';
        return w.toFixed(0) + ' W';
    },

    formatKw(w) {
        if (w === null || w === undefined) return '--';
        return (w / 1000).toFixed(2);
    },
};
