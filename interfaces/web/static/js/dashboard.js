/* ============================================================
   NetworkAgent Dashboard -- Dashboard View
   ============================================================ */

"use strict";

const Dashboard = {
    cpuChart: null,
    memChart: null,
    _loading: false,

    async load() {
        if (this._loading) return;
        this._loading = true;
        try {
            await Promise.all([
                this.loadStatus(),
                this.loadDashboard(),
            ]);
        } finally {
            this._loading = false;
        }
    },

    async loadStatus() {
        try {
            const data = await API.get("/status");
            $("stat-total").textContent   = data.total_devices   ?? "--";
            $("stat-online").textContent  = data.online_devices  ?? "--";
            $("stat-offline").textContent = data.offline_devices ?? "--";
            $("stat-alerts").textContent  = data.active_alerts   ?? "--";

            // Update alert badge in navbar
            const badge = $("alert-badge");
            if (data.active_alerts > 0) {
                badge.textContent = data.active_alerts;
                badge.classList.remove("hidden");
            } else {
                badge.classList.add("hidden");
            }
        } catch (e) { /* Toast already shown by API helper */ }
    },

    async loadDashboard() {
        try {
            const data = await API.get("/monitoring/dashboard");

            // ---- CPU gauge ----
            if (this.cpuChart) Charts.destroy(this.cpuChart);
            this.cpuChart = Charts.createGaugeChart(
                $("chart-cpu"),
                data.avg_cpu || 0,
                "CPU",
                data.avg_cpu > 80 ? "#ef4444" : data.avg_cpu > 60 ? "#eab308" : "#22c55e"
            );

            // ---- Memory gauge ----
            if (this.memChart) Charts.destroy(this.memChart);
            this.memChart = Charts.createGaugeChart(
                $("chart-mem"),
                data.avg_memory || 0,
                "Memory",
                data.avg_memory > 80 ? "#ef4444" : data.avg_memory > 60 ? "#eab308" : "#3b82f6"
            );

            // ---- Recent alerts table ----
            this.renderRecentAlerts(data.recent_alerts || []);

            // ---- Top devices table ----
            await this.renderTopDevices();

        } catch (e) { /* handled */ }
    },

    renderRecentAlerts(alerts) {
        const tbody = $("recent-alerts-table").querySelector("tbody");
        tbody.innerHTML = "";
        if (alerts.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No recent alerts</td></tr>';
            return;
        }
        alerts.slice(0, 8).forEach(a => {
            const row = document.createElement("tr");
            row.innerHTML = `
                <td>${formatDate(a.timestamp || a.created_at)}</td>
                <td>${escapeHTML(a.device_id || a.device_hostname || "--")}</td>
                <td>${badgeHTML(a.severity)}</td>
                <td>${escapeHTML(a.message || "")}</td>
                <td>${badgeHTML(a.status)}</td>`;
            tbody.appendChild(row);
        });
    },

    async renderTopDevices() {
        try {
            const data = await API.get("/devices");
            AppState.devices = data.devices || [];
            const tbody = $("top-devices-table").querySelector("tbody");
            tbody.innerHTML = "";
            if (AppState.devices.length === 0) {
                tbody.innerHTML = '<tr><td colspan="2" style="text-align:center;color:var(--text-muted)">No devices</td></tr>';
                return;
            }
            AppState.devices.slice(0, 10).forEach(d => {
                const row = document.createElement("tr");
                row.innerHTML = `
                    <td>${escapeHTML(d.hostname || d.ip_address || "--")}</td>
                    <td>${badgeHTML(d.status || "unknown")}</td>`;
                tbody.appendChild(row);
            });
        } catch (e) { /* handled */ }
    },
};
