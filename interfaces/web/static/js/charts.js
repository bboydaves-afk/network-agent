/* ============================================================
   NetworkAgent Dashboard -- Chart Utilities (Chart.js)
   ============================================================ */

"use strict";

const Charts = {
    // Color palette matching the dark theme
    colors: [
        "#3b82f6",  // blue
        "#22c55e",  // green
        "#eab308",  // yellow
        "#ef4444",  // red
        "#a855f7",  // purple
        "#06b6d4",  // cyan
        "#f97316",  // orange
        "#ec4899",  // pink
    ],

    // Shared defaults for all charts
    defaults: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 400 },
        plugins: {
            legend: {
                labels: {
                    color: "#94a3b8",
                    font: { size: 11 },
                },
            },
            tooltip: {
                backgroundColor: "#1a2234",
                titleColor: "#e2e8f0",
                bodyColor: "#94a3b8",
                borderColor: "#2a3650",
                borderWidth: 1,
                cornerRadius: 6,
                padding: 10,
            },
        },
        scales: {
            x: {
                ticks: { color: "#64748b", font: { size: 10 } },
                grid:  { color: "rgba(42,54,80,0.5)", drawBorder: false },
            },
            y: {
                ticks: { color: "#64748b", font: { size: 10 } },
                grid:  { color: "rgba(42,54,80,0.5)", drawBorder: false },
                beginAtZero: true,
            },
        },
    },

    /**
     * Create a time-series line chart.
     * @param {HTMLCanvasElement} canvas
     * @param {Array} datasets  - Chart.js dataset objects with {label, data: [{x,y}], ...}
     * @param {string} title
     * @returns {Chart}
     */
    createTimeSeriesChart(canvas, datasets, title = "") {
        const ctx = canvas.getContext("2d");
        return new Chart(ctx, {
            type: "line",
            data: { datasets },
            options: {
                ...this.defaults,
                plugins: {
                    ...this.defaults.plugins,
                    title: {
                        display: !!title,
                        text: title,
                        color: "#e2e8f0",
                        font: { size: 13, weight: "600" },
                        padding: { bottom: 12 },
                    },
                },
                scales: {
                    x: {
                        type: "time",
                        time: { tooltipFormat: "PPpp" },
                        ticks: { color: "#64748b", font: { size: 10 }, maxTicksLimit: 12 },
                        grid: { color: "rgba(42,54,80,0.5)", drawBorder: false },
                    },
                    y: {
                        ...this.defaults.scales.y,
                    },
                },
            },
        });
    },

    /**
     * Create a simple line chart with string labels (non-time x axis).
     * @param {HTMLCanvasElement} canvas
     * @param {Array<string>} labels
     * @param {Array} datasets
     * @param {string} title
     * @returns {Chart}
     */
    createLineChart(canvas, labels, datasets, title = "") {
        const ctx = canvas.getContext("2d");
        return new Chart(ctx, {
            type: "line",
            data: { labels, datasets },
            options: {
                ...this.defaults,
                plugins: {
                    ...this.defaults.plugins,
                    title: {
                        display: !!title,
                        text: title,
                        color: "#e2e8f0",
                        font: { size: 13, weight: "600" },
                    },
                },
            },
        });
    },

    /**
     * Create a doughnut/gauge chart (e.g. CPU or Memory usage).
     * @param {HTMLCanvasElement} canvas
     * @param {number} value  - Percentage 0-100
     * @param {string} label
     * @param {string} color
     * @returns {Chart}
     */
    createGaugeChart(canvas, value, label = "", color = "#3b82f6") {
        const ctx = canvas.getContext("2d");
        const remaining = Math.max(0, 100 - value);
        return new Chart(ctx, {
            type: "doughnut",
            data: {
                labels: [label, "Remaining"],
                datasets: [{
                    data: [value, remaining],
                    backgroundColor: [color, "rgba(42,54,80,0.4)"],
                    borderWidth: 0,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "75%",
                animation: { duration: 600 },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        ...this.defaults.plugins.tooltip,
                    },
                },
            },
            plugins: [{
                id: "centerText",
                afterDraw(chart) {
                    const { ctx: c, width, height } = chart;
                    c.save();
                    c.font = "bold 1.6rem 'Segoe UI', sans-serif";
                    c.fillStyle = "#e2e8f0";
                    c.textAlign = "center";
                    c.textBaseline = "middle";
                    c.fillText(`${Math.round(value)}%`, width / 2, height / 2);
                    c.restore();
                },
            }],
        });
    },

    /**
     * Create a bar chart for interface utilization or similar.
     * @param {HTMLCanvasElement} canvas
     * @param {Array<string>} labels
     * @param {Array<number>} values
     * @param {string} title
     * @param {string} color
     * @returns {Chart}
     */
    createBarChart(canvas, labels, values, title = "", color = "#3b82f6") {
        const ctx = canvas.getContext("2d");
        return new Chart(ctx, {
            type: "bar",
            data: {
                labels,
                datasets: [{
                    label: title || "Value",
                    data: values,
                    backgroundColor: color + "99",
                    borderColor: color,
                    borderWidth: 1,
                    borderRadius: 4,
                }],
            },
            options: {
                ...this.defaults,
                plugins: {
                    ...this.defaults.plugins,
                    legend: { display: false },
                    title: {
                        display: !!title,
                        text: title,
                        color: "#e2e8f0",
                        font: { size: 13, weight: "600" },
                    },
                },
            },
        });
    },

    /**
     * Update an existing chart's dataset data and re-render.
     * @param {Chart} chart
     * @param {number} datasetIndex
     * @param {Array} newData
     * @param {Array<string>} newLabels  - optional, for category charts
     */
    updateData(chart, datasetIndex, newData, newLabels = null) {
        if (!chart) return;
        if (newLabels) chart.data.labels = newLabels;
        if (chart.data.datasets[datasetIndex]) {
            chart.data.datasets[datasetIndex].data = newData;
        }
        chart.update("none"); // no animation on update
    },

    /**
     * Safely destroy a chart instance.
     * @param {Chart} chart
     */
    destroy(chart) {
        if (chart) chart.destroy();
    },

    /**
     * Pick a color from the palette by index.
     * @param {number} index
     * @returns {string}
     */
    colorAt(index) {
        return this.colors[index % this.colors.length];
    },
};
