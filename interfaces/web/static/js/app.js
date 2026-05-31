/* ============================================================
   NetworkAgent Dashboard -- Main Application JS
   ============================================================ */

"use strict";

// ---------------------------------------------------------------------------
// Global State
// ---------------------------------------------------------------------------
const AppState = {
    currentView: "dashboard",
    devices: [],
    alerts: [],
    alertRules: [],
    wsMetrics: null,
    wsAlerts: null,
    wsChat: null,
    refreshTimer: null,
};

// ---------------------------------------------------------------------------
// API Helper
// ---------------------------------------------------------------------------
const API = {
    base: "/api",

    async request(method, path, body = null) {
        const opts = {
            method,
            headers: { "Content-Type": "application/json" },
        };
        if (body !== null) {
            opts.body = JSON.stringify(body);
        }
        try {
            const resp = await fetch(this.base + path, opts);
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ detail: resp.statusText }));
                throw new Error(err.detail || resp.statusText);
            }
            if (resp.status === 204) return null;
            return await resp.json();
        } catch (e) {
            Toast.error(e.message || "API request failed");
            throw e;
        }
    },

    get(path)        { return this.request("GET",    path); },
    post(path, body) { return this.request("POST",   path, body); },
    put(path, body)  { return this.request("PUT",    path, body); },
    del(path)        { return this.request("DELETE", path); },
};

// ---------------------------------------------------------------------------
// Toast Notification System
// ---------------------------------------------------------------------------
const Toast = {
    _container: null,

    _getContainer() {
        if (!this._container) {
            this._container = document.getElementById("toast-container");
        }
        return this._container;
    },

    show(message, type = "info") {
        const el = document.createElement("div");
        el.className = `toast ${type}`;
        el.textContent = message;
        this._getContainer().appendChild(el);
        setTimeout(() => el.remove(), 5000);
    },

    success(msg) { this.show(msg, "success"); },
    error(msg)   { this.show(msg, "error"); },
    warning(msg) { this.show(msg, "warning"); },
    info(msg)    { this.show(msg, "info"); },
};

// ---------------------------------------------------------------------------
// Router / Navigation
// ---------------------------------------------------------------------------
function navigateTo(viewName) {
    // Update sidebar active state
    document.querySelectorAll(".nav-link").forEach(link => {
        link.classList.toggle("active", link.dataset.view === viewName);
    });

    // Show / hide view panels
    document.querySelectorAll(".view").forEach(v => {
        v.classList.toggle("active", v.id === `view-${viewName}`);
    });

    AppState.currentView = viewName;

    // Trigger view-specific load
    switch (viewName) {
        case "dashboard":   Dashboard.load();    break;
        case "devices":     Devices.load();      break;
        case "configs":     Configs.load();      break;
        case "monitoring":  Monitoring.load();   break;
        case "alerts":      Alerts.load();       break;
        case "discovery":   break; // static form
        case "diagnostics": Diagnostics.load();  break;
        case "automation":  Automation.load();   break;
        case "sites":       Sites.load();        break;
        case "topology":    Topology.load();     break;
        case "firmware":    Firmware.load();     break;
        case "ipam":        IPAM.load();         break;
        case "traffic":     Traffic.load();      break;
        case "syslog":      Syslog.load();       break;
        case "compliance":  Compliance.load();   break;
        case "changes":     Changes.load();      break;
        case "credentials": Credentials.load();  break;
        case "firewall":    Firewall.load();     break;
        case "chat":        Chat.init();         break;
    }
}

// ---------------------------------------------------------------------------
// WebSocket Manager
// ---------------------------------------------------------------------------
const WS = {
    reconnectDelay: 3000,
    maxReconnect: 50,

    connect(path, channel, onMessage) {
        let attempts = 0;

        const _connect = () => {
            const protocol = location.protocol === "https:" ? "wss:" : "ws:";
            const url = `${protocol}//${location.host}${path}`;
            const ws = new WebSocket(url);

            ws.onopen = () => {
                attempts = 0;
                this._updateIndicator(true);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type !== "pong") {
                        onMessage(data);
                    }
                } catch (e) { /* ignore non-JSON */ }
            };

            ws.onclose = () => {
                this._updateIndicator(false);
                if (attempts < this.maxReconnect) {
                    attempts++;
                    setTimeout(_connect, this.reconnectDelay);
                }
            };

            ws.onerror = () => { ws.close(); };

            // Keepalive ping every 30 seconds
            const pingInterval = setInterval(() => {
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send("ping");
                } else {
                    clearInterval(pingInterval);
                }
            }, 30000);

            return ws;
        };

        return _connect();
    },

    _updateIndicator(connected) {
        const dot   = document.getElementById("ws-indicator");
        const label = document.getElementById("ws-label");
        if (connected) {
            dot.classList.remove("offline");
            dot.classList.add("online");
            label.textContent = "Connected";
        } else {
            dot.classList.remove("online");
            dot.classList.add("offline");
            label.textContent = "Disconnected";
        }
    },
};

// ---------------------------------------------------------------------------
// Utility Helpers
// ---------------------------------------------------------------------------
function $(id) { return document.getElementById(id); }

function badgeHTML(value, prefix = "badge") {
    const cls = `${prefix}-${(value || "unknown").toLowerCase().replace(/\s+/g, "-")}`;
    return `<span class="badge ${cls}">${value || "unknown"}</span>`;
}

function formatDate(ts) {
    if (!ts) return "--";
    const d = new Date(ts);
    return d.toLocaleString();
}

function escapeHTML(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function populateDeviceSelect(selectId, devices) {
    const sel = $(selectId);
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="">Select Device...</option>';
    (devices || AppState.devices).forEach(d => {
        const id   = d.id || d.device_id;
        const name = d.hostname || d.ip_address || id;
        const opt  = document.createElement("option");
        opt.value = id;
        opt.textContent = name;
        sel.appendChild(opt);
    });
    if (current) sel.value = current;
}

// ---------------------------------------------------------------------------
// Clock
// ---------------------------------------------------------------------------
function startClock() {
    const el = $("clock");
    const tick = () => {
        el.textContent = new Date().toLocaleTimeString();
    };
    tick();
    setInterval(tick, 1000);
}

// ---------------------------------------------------------------------------
// Configs View
// ---------------------------------------------------------------------------
const Configs = {
    selectedBackups: [],

    async load() {
        const data = await API.get("/devices");
        AppState.devices = data.devices || [];
        populateDeviceSelect("config-device-select", AppState.devices);
    },

    async loadHistory(deviceId) {
        if (!deviceId) return;
        const data = await API.get(`/configs/history/${deviceId}`);
        const backups = data.backups || [];
        this.selectedBackups = [];
        const tbody = $("config-history-table").querySelector("tbody");
        tbody.innerHTML = "";
        if (backups.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">No backups found</td></tr>';
            return;
        }
        backups.forEach(b => {
            const id = b.id || b.backup_id;
            const row = document.createElement("tr");
            row.innerHTML = `
                <td>${formatDate(b.timestamp || b.created_at)}</td>
                <td>${escapeHTML(b.config_type || "running")}</td>
                <td>${b.size || "--"}</td>
                <td>
                    <button class="btn btn-sm" onclick="Configs.viewBackup('${id}')">View</button>
                    <button class="btn btn-sm" onclick="Configs.toggleDiff('${id}')">Diff</button>
                    <button class="btn btn-sm btn-danger" onclick="Configs.rollback('${$("config-device-select").value}','${id}')">Rollback</button>
                </td>`;
            tbody.appendChild(row);
        });
    },

    async viewBackup(backupId) {
        const data = await API.get(`/configs/backup/${backupId}`);
        $("config-viewer").textContent = data.content || JSON.stringify(data, null, 2);
    },

    toggleDiff(backupId) {
        const idx = this.selectedBackups.indexOf(backupId);
        if (idx >= 0) {
            this.selectedBackups.splice(idx, 1);
        } else {
            this.selectedBackups.push(backupId);
        }
        if (this.selectedBackups.length === 2) {
            this.showDiff(this.selectedBackups[0], this.selectedBackups[1]);
            this.selectedBackups = [];
        } else if (this.selectedBackups.length === 1) {
            Toast.info("Select a second backup to compare");
        }
    },

    async showDiff(id1, id2) {
        const data = await API.get(`/configs/diff/${id1}/${id2}`);
        $("config-viewer").textContent = data.diff || "No differences found.";
    },

    async backupDevice() {
        const deviceId = $("config-device-select").value;
        if (!deviceId) { Toast.warning("Select a device first"); return; }
        await API.post(`/configs/backup/${deviceId}`);
        Toast.success("Backup created");
        this.loadHistory(deviceId);
    },

    async backupAll() {
        await API.post("/configs/backup-all");
        Toast.success("All backups triggered");
    },

    async deploy() {
        const deviceId = $("config-device-select").value;
        if (!deviceId) { Toast.warning("Select a device first"); return; }
        const cmds = $("deploy-commands").value.trim().split("\n").filter(c => c);
        if (cmds.length === 0) { Toast.warning("Enter at least one command"); return; }
        const dryRun = $("deploy-dryrun").checked;
        const data = await API.post(`/configs/deploy/${deviceId}`, { commands: cmds, dry_run: dryRun });
        $("deploy-result").textContent = JSON.stringify(data, null, 2);
        Toast.success(dryRun ? "Dry run complete" : "Deployed successfully");
    },

    async rollback(deviceId, backupId) {
        if (!confirm("Rollback to this configuration?")) return;
        await API.post(`/configs/rollback/${deviceId}/${backupId}`);
        Toast.success("Rollback complete");
    },
};

// ---------------------------------------------------------------------------
// Monitoring View
// ---------------------------------------------------------------------------
const Monitoring = {
    chart: null,

    async load() {
        const data = await API.get("/devices");
        AppState.devices = data.devices || [];
        populateDeviceSelect("mon-device-select", AppState.devices);
    },

    async loadMetrics() {
        const deviceId   = $("mon-device-select").value;
        const metricName = $("mon-metric-select").value;
        const hours      = $("mon-hours-select").value;
        if (!deviceId) { Toast.warning("Select a device"); return; }
        let url = `/monitoring/metrics/${deviceId}?hours=${hours}`;
        if (metricName) url += `&metric_name=${metricName}`;
        const data = await API.get(url);
        this.renderChart(data.metrics || []);
    },

    renderChart(metrics) {
        if (this.chart) this.chart.destroy();
        // Group metrics by name
        const groups = {};
        metrics.forEach(m => {
            const name = m.metric_name || m.name || "value";
            if (!groups[name]) groups[name] = [];
            groups[name].push({ x: new Date(m.timestamp || m.created_at), y: m.value });
        });
        const datasets = Object.entries(groups).map(([name, points], i) => ({
            label: name,
            data: points.sort((a, b) => a.x - b.x),
            borderColor: Charts.colors[i % Charts.colors.length],
            backgroundColor: Charts.colors[i % Charts.colors.length] + "33",
            fill: true,
            tension: 0.3,
            pointRadius: 2,
        }));
        this.chart = Charts.createTimeSeriesChart($("chart-metrics"), datasets, "Metrics");
    },

    async pollDevice() {
        const deviceId = $("mon-device-select").value;
        if (!deviceId) { Toast.warning("Select a device"); return; }
        await API.post(`/monitoring/poll/${deviceId}`);
        Toast.success("Poll triggered");
        this.loadMetrics();
    },

    async pollAll() {
        const data = await API.post("/monitoring/poll-all");
        Toast.success(`Polled ${data.succeeded}/${data.total} devices`);
    },
};

// ---------------------------------------------------------------------------
// Alerts View
// ---------------------------------------------------------------------------
const Alerts = {
    async load() {
        await Promise.all([this.loadAlerts(), this.loadRules()]);
    },

    async loadAlerts() {
        const status = $("alert-filter-status").value;
        let url = "/alerts";
        if (status) url += `?status_filter=${status}`;
        const data = await API.get(url);
        AppState.alerts = data.alerts || [];
        const tbody = $("alerts-table").querySelector("tbody");
        tbody.innerHTML = "";
        if (AppState.alerts.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">No alerts</td></tr>';
            return;
        }
        AppState.alerts.forEach(a => {
            const id = a.id || a.alert_id;
            const row = document.createElement("tr");
            row.innerHTML = `
                <td>${formatDate(a.timestamp || a.created_at)}</td>
                <td>${escapeHTML(a.device_id || a.device_hostname || "--")}</td>
                <td>${escapeHTML(a.rule_name || "--")}</td>
                <td>${badgeHTML(a.severity)}</td>
                <td>${escapeHTML(a.message || "")}</td>
                <td>${badgeHTML(a.status)}</td>
                <td>
                    ${a.status === "active" ? `<button class="btn btn-sm" onclick="Alerts.ack('${id}')">Ack</button>` : ""}
                    ${a.status !== "resolved" ? `<button class="btn btn-sm btn-success" onclick="Alerts.resolve('${id}')">Resolve</button>` : ""}
                </td>`;
            tbody.appendChild(row);
        });

        // Update badge
        const activeCount = AppState.alerts.filter(a => a.status === "active").length;
        const badge = $("alert-badge");
        if (activeCount > 0) {
            badge.textContent = activeCount;
            badge.classList.remove("hidden");
        } else {
            badge.classList.add("hidden");
        }
    },

    async loadRules() {
        const data = await API.get("/alerts/rules");
        AppState.alertRules = data.rules || [];
        const tbody = $("rules-table").querySelector("tbody");
        tbody.innerHTML = "";
        if (AppState.alertRules.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">No rules</td></tr>';
            return;
        }
        AppState.alertRules.forEach(r => {
            const id = r.id || r.rule_id;
            const row = document.createElement("tr");
            row.innerHTML = `
                <td>${escapeHTML(r.name || "--")}</td>
                <td>${escapeHTML(r.metric_name || "--")}</td>
                <td>${r.condition || ">"} ${r.threshold}</td>
                <td><button class="btn btn-sm btn-danger" onclick="Alerts.deleteRule('${id}')">Del</button></td>`;
            tbody.appendChild(row);
        });
    },

    async ack(alertId) {
        await API.post(`/alerts/${alertId}/acknowledge`);
        Toast.success("Alert acknowledged");
        this.loadAlerts();
    },

    async resolve(alertId) {
        await API.post(`/alerts/${alertId}/resolve`);
        Toast.success("Alert resolved");
        this.loadAlerts();
    },

    async createRule(formData) {
        await API.post("/alerts/rules", formData);
        Toast.success("Rule created");
        $("rule-modal").classList.add("hidden");
        this.loadRules();
    },

    async deleteRule(ruleId) {
        if (!confirm("Delete this alert rule?")) return;
        await API.del(`/alerts/rules/${ruleId}`);
        Toast.success("Rule deleted");
        this.loadRules();
    },
};

// ---------------------------------------------------------------------------
// Discovery View
// ---------------------------------------------------------------------------
const Discovery = {
    async snmpScan() {
        const subnet    = $("disc-subnet").value.trim();
        const community = $("disc-community").value.trim();
        const timeout   = parseInt($("disc-timeout").value, 10);
        if (!subnet) { Toast.warning("Enter a subnet"); return; }
        this._setStatus("Scanning...", "loading");
        try {
            const data = await API.post("/discovery/scan", { subnet, community, timeout });
            this._renderResults(data.devices || []);
            this._setStatus(`Found ${data.discovered} device(s)`, "success");
        } catch (e) {
            this._setStatus("Scan failed", "error");
        }
    },

    async pingSweep() {
        const subnet = $("disc-subnet").value.trim();
        if (!subnet) { Toast.warning("Enter a subnet"); return; }
        this._setStatus("Running ping sweep...", "loading");
        try {
            const data = await API.post("/discovery/ping-sweep", { subnet });
            this._renderResults(data.results || []);
            this._setStatus(`${data.alive} host(s) alive out of ${data.total_scanned}`, "success");
        } catch (e) {
            this._setStatus("Ping sweep failed", "error");
        }
    },

    async autoDiscover() {
        const subnet    = $("disc-subnet").value.trim();
        const community = $("disc-community").value.trim();
        if (!subnet) { Toast.warning("Enter a subnet"); return; }
        this._setStatus("Auto-discovering...", "loading");
        try {
            const data = await API.post("/discovery/auto-discover", { subnet, community });
            Toast.success(`Added ${data.added} device(s), skipped ${data.skipped}`);
            this._renderResults(data.added_devices || []);
            this._setStatus(`Discovered ${data.discovered}, added ${data.added}, skipped ${data.skipped}`, "success");
        } catch (e) {
            this._setStatus("Auto-discover failed", "error");
        }
    },

    _setStatus(msg, type) {
        const el = $("discovery-status");
        el.textContent = msg;
        el.className = `status-message ${type}`;
        el.classList.remove("hidden");
    },

    _renderResults(items) {
        const tbody = $("discovery-table").querySelector("tbody");
        tbody.innerHTML = "";
        if (items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No results</td></tr>';
            return;
        }
        items.forEach(d => {
            const row = document.createElement("tr");
            row.innerHTML = `
                <td>${escapeHTML(d.ip || d.ip_address || "--")}</td>
                <td>${escapeHTML(d.hostname || "--")}</td>
                <td>${escapeHTML(d.vendor || "--")}</td>
                <td>${escapeHTML(d.device_type || d.type || "--")}</td>
                <td>${d.alive !== undefined ? (d.alive ? badgeHTML("online") : badgeHTML("offline")) : badgeHTML(d.status || "discovered")}</td>`;
            tbody.appendChild(row);
        });
    },
};

// ---------------------------------------------------------------------------
// Diagnostics View
// ---------------------------------------------------------------------------
const Diagnostics = {
    async load() {
        const data = await API.get("/devices");
        AppState.devices = data.devices || [];
        populateDeviceSelect("diag-ping-source",    AppState.devices);
        populateDeviceSelect("diag-trace-source",   AppState.devices);
        populateDeviceSelect("diag-health-device",  AppState.devices);
    },

    async ping() {
        const target   = $("diag-ping-target").value.trim();
        const count    = parseInt($("diag-ping-count").value, 10);
        const sourceId = $("diag-ping-source").value;
        if (!target) { Toast.warning("Enter a target"); return; }
        $("diag-ping-result").textContent = "Running...";
        const body = { target, count };
        if (sourceId) body.source_device_id = sourceId;
        const data = await API.post("/diag/ping", body);
        $("diag-ping-result").textContent = JSON.stringify(data, null, 2);
    },

    async traceroute() {
        const target   = $("diag-trace-target").value.trim();
        const sourceId = $("diag-trace-source").value;
        if (!target) { Toast.warning("Enter a target"); return; }
        $("diag-trace-result").textContent = "Running...";
        const body = { target };
        if (sourceId) body.source_device_id = sourceId;
        const data = await API.post("/diag/traceroute", body);
        $("diag-trace-result").textContent = JSON.stringify(data, null, 2);
    },

    async portCheck() {
        const target  = $("diag-port-target").value.trim();
        const port    = parseInt($("diag-port-number").value, 10);
        const timeout = parseInt($("diag-port-timeout").value, 10);
        if (!target) { Toast.warning("Enter a target"); return; }
        $("diag-port-result").textContent = "Checking...";
        const data = await API.post("/diag/port-check", { target, port, timeout });
        $("diag-port-result").textContent = JSON.stringify(data, null, 2);
    },

    async dnsLookup() {
        const hostname = $("diag-dns-host").value.trim();
        if (!hostname) { Toast.warning("Enter a hostname"); return; }
        $("diag-dns-result").textContent = "Resolving...";
        const data = await API.post("/diag/dns", { hostname });
        $("diag-dns-result").textContent = JSON.stringify(data, null, 2);
    },

    async healthCheck() {
        const deviceId = $("diag-health-device").value;
        if (!deviceId) { Toast.warning("Select a device"); return; }
        $("diag-health-result").textContent = "Checking...";
        const data = await API.get(`/diag/health/${deviceId}`);
        $("diag-health-result").textContent = JSON.stringify(data, null, 2);
    },
};

// ---------------------------------------------------------------------------
// Chat Interface
// ---------------------------------------------------------------------------
const Chat = {
    ws: null,
    initialized: false,

    init() {
        if (this.initialized) return;
        this.initialized = true;
        this.connectWS();
    },

    connectWS() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${protocol}//${location.host}/ws/chat`;
        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            Toast.info("AI Chat connected");
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.status === "thinking") {
                    this._addThinking();
                } else {
                    this._removeThinking();
                    this._addBubble(data.role || "assistant", data.content || "");
                }
            } catch (e) { /* ignore */ }
        };

        this.ws.onclose = () => {
            // Reconnect after delay
            setTimeout(() => {
                if (AppState.currentView === "chat") {
                    this.connectWS();
                }
            }, 3000);
        };
    },

    send(message) {
        if (!message.trim()) return;
        this._addBubble("user", message);
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ content: message }));
        } else {
            this._addBubble("system", "Not connected. Reconnecting...");
            this.connectWS();
        }
    },

    _addBubble(role, content) {
        const container = $("chat-messages");
        const div = document.createElement("div");
        div.className = `chat-bubble ${role}`;
        div.textContent = content;
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    },

    _addThinking() {
        this._removeThinking();
        const container = $("chat-messages");
        const div = document.createElement("div");
        div.className = "chat-bubble thinking";
        div.id = "chat-thinking";
        div.textContent = "Thinking...";
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    },

    _removeThinking() {
        const el = $("chat-thinking");
        if (el) el.remove();
    },
};

// ---------------------------------------------------------------------------
// Event Bindings
// ---------------------------------------------------------------------------
function bindEvents() {
    // Sidebar navigation
    document.querySelectorAll(".nav-link").forEach(link => {
        link.addEventListener("click", (e) => {
            e.preventDefault();
            navigateTo(link.dataset.view);
        });
    });

    // ---- Configs ----
    $("config-device-select").addEventListener("change", (e) => {
        if (e.target.value) Configs.loadHistory(e.target.value);
    });
    $("btn-backup-config").addEventListener("click", () => Configs.backupDevice());
    $("btn-backup-all").addEventListener("click", () => Configs.backupAll());
    $("btn-deploy").addEventListener("click", () => Configs.deploy());

    // ---- Monitoring ----
    $("mon-device-select").addEventListener("change", () => Monitoring.loadMetrics());
    $("mon-metric-select").addEventListener("change", () => Monitoring.loadMetrics());
    $("mon-hours-select").addEventListener("change", () => Monitoring.loadMetrics());
    $("btn-poll-device").addEventListener("click", () => Monitoring.pollDevice());
    $("btn-poll-all").addEventListener("click", () => Monitoring.pollAll());

    // ---- Alerts ----
    $("alert-filter-status").addEventListener("change", () => Alerts.loadAlerts());
    $("btn-add-rule").addEventListener("click", () => $("rule-modal").classList.remove("hidden"));
    $("rule-modal-close").addEventListener("click", () => $("rule-modal").classList.add("hidden"));
    $("btn-cancel-rule").addEventListener("click", () => $("rule-modal").classList.add("hidden"));
    $("rule-form").addEventListener("submit", (e) => {
        e.preventDefault();
        Alerts.createRule({
            name:        $("rf-name").value,
            metric_name: $("rf-metric").value,
            condition:   $("rf-condition").value,
            threshold:   parseFloat($("rf-threshold").value),
            severity:    $("rf-severity").value,
        });
    });

    // ---- Discovery ----
    $("btn-snmp-scan").addEventListener("click", () => Discovery.snmpScan());
    $("btn-ping-sweep").addEventListener("click", () => Discovery.pingSweep());
    $("btn-auto-discover").addEventListener("click", () => Discovery.autoDiscover());

    // ---- Diagnostics ----
    $("btn-diag-ping").addEventListener("click", () => Diagnostics.ping());
    $("btn-diag-trace").addEventListener("click", () => Diagnostics.traceroute());
    $("btn-diag-port").addEventListener("click", () => Diagnostics.portCheck());
    $("btn-diag-dns").addEventListener("click", () => Diagnostics.dnsLookup());
    $("btn-diag-health").addEventListener("click", () => Diagnostics.healthCheck());

    // ---- Chat ----
    $("chat-form").addEventListener("submit", (e) => {
        e.preventDefault();
        const input = $("chat-input");
        Chat.send(input.value);
        input.value = "";
    });
}

// ---------------------------------------------------------------------------
// WebSocket Streams
// ---------------------------------------------------------------------------
function connectStreams() {
    AppState.wsMetrics = WS.connect("/ws/metrics", "metrics", (data) => {
        // If we're on the dashboard or monitoring view, refresh
        if (data.type === "metric_update" && AppState.currentView === "dashboard") {
            Dashboard.load();
        }
    });

    AppState.wsAlerts = WS.connect("/ws/alerts", "alerts", (data) => {
        if (data.type === "alert_update" || data.type === "new_alert") {
            if (AppState.currentView === "alerts") {
                Alerts.loadAlerts();
            }
            // Update badge
            API.get("/alerts?status_filter=active").then(d => {
                const count = (d.alerts || []).length;
                const badge = $("alert-badge");
                if (count > 0) {
                    badge.textContent = count;
                    badge.classList.remove("hidden");
                } else {
                    badge.classList.add("hidden");
                }
            }).catch(() => {});

            if (data.type === "new_alert") {
                Toast.warning(`New alert: ${data.data?.message || "Check alerts"}`);
            }
        }
    });
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    startClock();
    bindEvents();
    connectStreams();
    navigateTo("dashboard");

    // Auto-refresh current view every 30 seconds
    AppState.refreshTimer = setInterval(() => {
        if (AppState.currentView === "dashboard") Dashboard.load();
    }, 30000);
});
