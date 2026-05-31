/* ============================================================
   NetworkAgent -- Feature View Controllers
   Sites, Topology, Firmware, IPAM, Traffic, Syslog,
   Compliance, Changes, Credentials
   ============================================================ */
"use strict";

// ---------------------------------------------------------------------------
// Sites
// ---------------------------------------------------------------------------
const Sites = {
    async load() {
        const data = await API.get("/sites/summaries");
        const sites = data.sites || [];
        const tbody = $("sites-table").querySelector("tbody");
        tbody.innerHTML = "";
        if (sites.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">No sites configured</td></tr>';
            return;
        }
        sites.forEach(s => {
            const row = document.createElement("tr");
            row.innerHTML = `
                <td><strong>${escapeHTML(s.name || "")}</strong></td>
                <td>${escapeHTML(s.location || "")}</td>
                <td>${escapeHTML(s.region || "")}</td>
                <td>${s.total_devices || 0}</td>
                <td><span class="badge badge-online">${s.online || 0}</span></td>
                <td>${escapeHTML(s.contact || "")}</td>
                <td>
                    <button class="btn btn-sm" onclick="Sites.viewDevices('${s.id}')">Devices</button>
                    <button class="btn btn-sm btn-danger" onclick="Sites.deleteSite('${s.id}')">Del</button>
                </td>`;
            tbody.appendChild(row);
        });
    },
    showAddModal() { $("sf-id").value = ""; $("sf-name").value = ""; $("sf-location").value = ""; $("sf-region").value = ""; $("sf-description").value = ""; $("sf-contact").value = ""; $("site-modal").classList.remove("hidden"); },
    async save() {
        const body = { name: $("sf-name").value, location: $("sf-location").value, region: $("sf-region").value, description: $("sf-description").value, contact: $("sf-contact").value };
        await API.post("/sites", body);
        Toast.success("Site created");
        $("site-modal").classList.add("hidden");
        this.load();
    },
    async viewDevices(siteId) { navigateTo("devices"); /* TODO: filter by site */ },
    async deleteSite(siteId) { if (!confirm("Delete this site?")) return; await API.del(`/sites/${siteId}`); Toast.success("Site deleted"); this.load(); },
};

// ---------------------------------------------------------------------------
// Topology
// ---------------------------------------------------------------------------
const Topology = {
    network: null,
    async load() {
        try {
            const data = await API.get("/topology/graph");
            this.renderGraph(data);
            this.renderTable(data.neighbors || []);
        } catch (e) { /* no topology data yet */ }
    },
    renderGraph(data) {
        const container = $("topology-graph");
        if (!container || typeof vis === "undefined") return;
        const nodes = new vis.DataSet((data.nodes || []).map(n => ({
            id: n.id, label: n.label || n.hostname || n.id.substring(0, 8),
            color: n.status === "online" ? "#22c55e" : n.status === "offline" ? "#ef4444" : "#64748b",
            shape: "dot", size: 20,
        })));
        const edges = new vis.DataSet((data.edges || []).map(e => ({
            from: e.from || e.source, to: e.to || e.target,
            label: e.label || "", color: { color: "#3b82f6" },
        })));
        if (this.network) this.network.destroy();
        this.network = new vis.Network(container, { nodes, edges }, {
            physics: { stabilization: { iterations: 100 } },
            nodes: { font: { color: "#e2e8f0", size: 12 } },
            edges: { font: { color: "#94a3b8", size: 10 }, smooth: { type: "continuous" } },
        });
    },
    renderTable(neighbors) {
        const tbody = $("topo-neighbor-table").querySelector("tbody");
        tbody.innerHTML = "";
        neighbors.forEach(n => {
            const row = document.createElement("tr");
            row.innerHTML = `<td>${escapeHTML(n.device_hostname || n.device_id || "")}</td><td>${escapeHTML(n.local_interface || "")}</td><td>${escapeHTML(n.neighbor_hostname || "")}</td><td>${escapeHTML(n.neighbor_port || "")}</td><td>${escapeHTML(n.neighbor_platform || "")}</td><td>${badgeHTML(n.protocol || "cdp")}</td>`;
            tbody.appendChild(row);
        });
    },
    async discover() {
        Toast.info("Discovering topology...");
        try { const data = await API.post("/topology/discover"); Toast.success(`Found ${data.total_neighbors || 0} neighbor(s)`); this.load(); }
        catch (e) { Toast.error("Discovery failed"); }
    },
    async saveSnapshot() {
        const name = prompt("Snapshot name:");
        if (!name) return;
        await API.post("/topology/snapshots", { name });
        Toast.success("Snapshot saved");
    },
};

// ---------------------------------------------------------------------------
// Firmware
// ---------------------------------------------------------------------------
const Firmware = {
    async load() { await Promise.all([this.loadStatus(), this.loadCatalog()]); },
    async loadStatus() {
        try {
            const data = await API.get("/firmware/status");
            const tbody = $("firmware-status-table").querySelector("tbody");
            tbody.innerHTML = "";
            (data.devices || []).forEach(d => {
                const statusCls = d.status === "compliant" ? "badge-online" : d.status === "eol" ? "badge-offline" : "badge-degraded";
                const row = document.createElement("tr");
                row.innerHTML = `<td>${escapeHTML(d.hostname || "")}</td><td>${escapeHTML(d.vendor || "")}</td><td>${escapeHTML(d.model || "")}</td><td>${escapeHTML(d.current_version || "")}</td><td>${escapeHTML(d.recommended_version || "--")}</td><td><span class="badge ${statusCls}">${d.status || "unknown"}</span></td>`;
                tbody.appendChild(row);
            });
        } catch (e) { /* no data */ }
    },
    async loadCatalog() {
        try {
            const data = await API.get("/firmware/catalog");
            const tbody = $("firmware-catalog-table").querySelector("tbody");
            tbody.innerHTML = "";
            (data.entries || []).forEach(e => {
                const row = document.createElement("tr");
                row.innerHTML = `<td>${escapeHTML(e.vendor || "")}</td><td>${escapeHTML(e.model_pattern || "")}</td><td>${escapeHTML(e.version || "")}</td><td>${formatDate(e.release_date)}</td><td>${e.eol_date ? formatDate(e.eol_date) : "--"}</td><td>${(JSON.parse(e.cve_list || "[]")).length}</td><td>${e.is_recommended ? "Yes" : ""}</td>`;
                tbody.appendChild(row);
            });
        } catch (e) { /* no data */ }
    },
    async checkAll() { Toast.info("Checking firmware..."); try { await API.post("/firmware/check"); Toast.success("Check complete"); this.loadStatus(); } catch(e) {} },
    showCatalogModal() { Toast.info("Use CLI: firmware catalog add"); },
};

// ---------------------------------------------------------------------------
// IPAM
// ---------------------------------------------------------------------------
const IPAM = {
    async load() {
        try {
            const data = await API.get("/ipam/subnets");
            const tbody = $("ipam-subnets-table").querySelector("tbody");
            tbody.innerHTML = "";
            (data.subnets || []).forEach(s => {
                const pct = s.utilization_percent != null ? s.utilization_percent.toFixed(1) + "%" : "--";
                const row = document.createElement("tr");
                row.innerHTML = `<td><strong>${escapeHTML(s.network || "")}/${s.prefix_length || ""}</strong></td><td>${escapeHTML(s.name || "")}</td><td>${s.vlan_id || "--"}</td><td>${escapeHTML(s.gateway || "")}</td><td><div class="progress-bar"><div class="progress-fill" style="width:${s.utilization_percent || 0}%"></div></div> ${pct}</td><td>${escapeHTML(s.site_name || "")}</td><td><button class="btn btn-sm" onclick="IPAM.viewAddresses('${s.id}')">IPs</button> <button class="btn btn-sm" onclick="IPAM.scanSubnet('${s.id}')">Scan</button></td>`;
                tbody.appendChild(row);
            });
        } catch (e) { /* no data */ }
    },
    async viewAddresses(subnetId) {
        const data = await API.get(`/ipam/subnets/${subnetId}/addresses`);
        const tbody = $("ipam-addresses-table").querySelector("tbody");
        tbody.innerHTML = "";
        (data.addresses || []).forEach(a => {
            const row = document.createElement("tr");
            row.innerHTML = `<td>${escapeHTML(a.address || "")}</td><td>${escapeHTML(a.hostname || "")}</td><td>${escapeHTML(a.mac_address || "")}</td><td>${escapeHTML(a.device_id || "")}</td><td>${escapeHTML(a.interface || "")}</td><td>${badgeHTML(a.status || "active")}</td><td>${formatDate(a.last_seen)}</td>`;
            tbody.appendChild(row);
        });
        $("ipam-addresses-panel").style.display = "block";
    },
    async scanSubnet(subnetId) { Toast.info("Scanning..."); try { await API.post(`/ipam/subnets/${subnetId}/scan`); Toast.success("Scan complete"); this.load(); } catch(e) {} },
    showAddSubnet() { Toast.info("Use CLI: ipam add-subnet"); },
};

// ---------------------------------------------------------------------------
// Traffic
// ---------------------------------------------------------------------------
const Traffic = {
    chart: null,
    async load() {
        const data = await API.get("/devices");
        AppState.devices = data.devices || [];
        populateDeviceSelect("traffic-device-select", AppState.devices);
        this.loadTopTalkers();
    },
    async loadTrends() {
        const deviceId = $("traffic-device-select").value;
        const hours = $("traffic-hours-select").value;
        if (!deviceId) { Toast.warning("Select a device"); return; }
        const data = await API.get(`/traffic/trends/${deviceId}?hours=${hours}`);
        if (this.chart) this.chart.destroy();
        const datasets = (data.interfaces || []).map((iface, i) => ({
            label: iface.name + " (in)", data: (iface.data_in || []).map(p => ({ x: new Date(p.timestamp), y: p.value })),
            borderColor: Charts.colors[i % Charts.colors.length], fill: false, tension: 0.3, pointRadius: 1,
        }));
        this.chart = Charts.createTimeSeriesChart($("chart-traffic"), datasets, "Bandwidth (bps)");
    },
    async loadTopTalkers() {
        try {
            const data = await API.get("/traffic/top-talkers");
            const tbody = $("traffic-top-table").querySelector("tbody");
            tbody.innerHTML = "";
            (data.interfaces || []).forEach(i => {
                const row = document.createElement("tr");
                row.innerHTML = `<td>${escapeHTML(i.hostname || "")}</td><td>${escapeHTML(i.interface || "")}</td><td>${(i.bps_in || 0).toLocaleString()}</td><td>${(i.bps_out || 0).toLocaleString()}</td><td>${(i.utilization || 0).toFixed(1)}%</td>`;
                tbody.appendChild(row);
            });
        } catch (e) { /* no data */ }
    },
};

// ---------------------------------------------------------------------------
// Syslog
// ---------------------------------------------------------------------------
const Syslog = {
    ws: null, live: false,
    SEVERITY_NAMES: ["Emergency","Alert","Critical","Error","Warning","Notice","Info","Debug"],
    async load() { this.search(); },
    async search() {
        const query = $("syslog-search").value;
        const severity = $("syslog-severity-filter").value;
        let url = "/syslog/messages?limit=200";
        if (query) url += `&query=${encodeURIComponent(query)}`;
        if (severity) url += `&severity=${severity}`;
        const data = await API.get(url);
        this.renderTable(data.messages || []);
    },
    renderTable(messages) {
        const tbody = $("syslog-table").querySelector("tbody");
        tbody.innerHTML = "";
        messages.forEach(m => {
            const sevName = this.SEVERITY_NAMES[m.severity] || m.severity;
            const sevCls = m.severity <= 3 ? "badge-offline" : m.severity <= 4 ? "badge-degraded" : "badge-online";
            const row = document.createElement("tr");
            row.innerHTML = `<td>${formatDate(m.timestamp)}</td><td><span class="badge ${sevCls}">${sevName}</span></td><td>${escapeHTML(m.hostname || "")}</td><td>${m.facility || ""}</td><td>${escapeHTML(m.message || "")}</td>`;
            tbody.appendChild(row);
        });
    },
    toggleLive() {
        if (this.live) { if (this.ws) this.ws.close(); this.live = false; Toast.info("Live stream stopped"); return; }
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        this.ws = new WebSocket(`${protocol}//${location.host}/ws/syslog`);
        this.ws.onmessage = (event) => { try { const m = JSON.parse(event.data); if (m.type !== "pong") this._appendRow(m); } catch(e) {} };
        this.live = true; Toast.info("Live stream started");
    },
    _appendRow(m) {
        const tbody = $("syslog-table").querySelector("tbody");
        const sevName = this.SEVERITY_NAMES[m.severity] || m.severity;
        const sevCls = m.severity <= 3 ? "badge-offline" : m.severity <= 4 ? "badge-degraded" : "badge-online";
        const row = document.createElement("tr");
        row.innerHTML = `<td>${formatDate(m.timestamp)}</td><td><span class="badge ${sevCls}">${sevName}</span></td><td>${escapeHTML(m.hostname || "")}</td><td>${m.facility || ""}</td><td>${escapeHTML(m.message || "")}</td>`;
        tbody.insertBefore(row, tbody.firstChild);
        if (tbody.children.length > 500) tbody.removeChild(tbody.lastChild);
    },
};

// ---------------------------------------------------------------------------
// Compliance
// ---------------------------------------------------------------------------
const Compliance = {
    async load() {
        try {
            const data = await API.get("/compliance/results");
            const results = data.results || [];
            let totalPassed = 0, totalFailed = 0;
            results.forEach(r => { totalPassed += r.passed || 0; totalFailed += r.failed || 0; });
            $("compliance-passed").textContent = totalPassed;
            $("compliance-failed").textContent = totalFailed;
            const total = totalPassed + totalFailed;
            $("compliance-score").textContent = total > 0 ? ((totalPassed / total) * 100).toFixed(1) + "%" : "--";
            const tbody = $("compliance-results-table").querySelector("tbody");
            tbody.innerHTML = "";
            results.forEach(r => {
                const score = r.total_checks > 0 ? ((r.passed / r.total_checks) * 100).toFixed(1) : 0;
                const row = document.createElement("tr");
                row.innerHTML = `<td>${escapeHTML(r.device_hostname || r.device_id || "")}</td><td>${escapeHTML(r.ruleset_name || "")}</td><td class="text-green">${r.passed || 0}</td><td class="text-red">${r.failed || 0}</td><td>${score}%</td><td>${formatDate(r.created_at)}</td><td><button class="btn btn-sm" onclick="Compliance.viewDetails('${r.id}')">Details</button></td>`;
                tbody.appendChild(row);
            });
        } catch (e) { /* no data */ }
    },
    async runAll() { Toast.info("Running compliance checks..."); try { await API.post("/compliance/run"); Toast.success("Checks complete"); this.load(); } catch(e) {} },
    async viewDetails(id) { const data = await API.get(`/compliance/results/${id}`); alert(JSON.stringify(data.details || [], null, 2)); },
    async exportReport() { Toast.info("Use CLI: compliance report"); },
};

// ---------------------------------------------------------------------------
// Changes
// ---------------------------------------------------------------------------
const Changes = {
    async load() {
        const status = $("change-status-filter").value;
        let url = "/changes";
        if (status) url += `?status=${status}`;
        try {
            const data = await API.get(url);
            const tbody = $("changes-table").querySelector("tbody");
            tbody.innerHTML = "";
            (data.requests || []).forEach(r => {
                const statusCls = r.status === "approved" ? "badge-online" : r.status === "rejected" ? "badge-offline" : r.status === "pending" ? "badge-degraded" : "";
                const row = document.createElement("tr");
                row.innerHTML = `<td>${escapeHTML(r.title || "")}</td><td>${escapeHTML(r.device_id || "")}</td><td>${escapeHTML(r.requested_by || "")}</td><td>${badgeHTML(r.priority || "normal")}</td><td><span class="badge ${statusCls}">${r.status || ""}</span></td><td>${formatDate(r.created_at)}</td><td>${r.status === "pending" ? `<button class="btn btn-sm btn-success" onclick="Changes.approve('${r.id}')">Approve</button> <button class="btn btn-sm btn-danger" onclick="Changes.reject('${r.id}')">Reject</button>` : ""} ${r.status === "approved" ? `<button class="btn btn-sm btn-primary" onclick="Changes.apply('${r.id}')">Apply</button>` : ""}</td>`;
                tbody.appendChild(row);
            });
        } catch (e) { /* no data */ }
    },
    async approve(id) { await API.post(`/changes/${id}/approve`, { approved_by: "admin" }); Toast.success("Approved"); this.load(); },
    async reject(id) { const reason = prompt("Reason for rejection:"); await API.post(`/changes/${id}/reject`, { rejected_by: "admin", reason }); Toast.success("Rejected"); this.load(); },
    async apply(id) { if (!confirm("Apply this change to the device?")) return; await API.post(`/changes/${id}/apply`); Toast.success("Applied"); this.load(); },
    showCreateModal() { Toast.info("Use CLI: change request"); },
};

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------
const Credentials = {
    async load() { await Promise.all([this.loadCredentials(), this.loadHistory()]); },
    async loadCredentials() {
        try {
            const data = await API.get("/credentials");
            const tbody = $("credentials-table").querySelector("tbody");
            tbody.innerHTML = "";
            (data.credentials || []).forEach(c => {
                const row = document.createElement("tr");
                row.innerHTML = `<td>${escapeHTML(c.name || "")}</td><td>${escapeHTML(c.username || "")}</td><td>${c.device_count || "--"}</td><td>${formatDate(c.last_rotated)}</td><td>${badgeHTML(c.status || "active")}</td><td><button class="btn btn-sm" onclick="Credentials.rotate('${c.id}')">Rotate</button> <button class="btn btn-sm" onclick="Credentials.verify('${c.id}')">Verify</button></td>`;
                tbody.appendChild(row);
            });
        } catch (e) { /* no data */ }
    },
    async loadHistory() {
        try {
            const data = await API.get("/credentials/rotations");
            const tbody = $("rotation-history-table").querySelector("tbody");
            tbody.innerHTML = "";
            (data.rotations || []).forEach(r => {
                const row = document.createElement("tr");
                row.innerHTML = `<td>${escapeHTML(r.credential_name || r.credential_id || "")}</td><td>${badgeHTML(r.status || "")}</td><td>${r.devices_updated || 0}</td><td>${r.devices_failed || 0}</td><td>${formatDate(r.started_at)}</td><td>${formatDate(r.completed_at)}</td>`;
                tbody.appendChild(row);
            });
        } catch (e) { /* no data */ }
    },
    async rotate(id) { if (!confirm("Rotate this credential?")) return; Toast.info("Rotating..."); try { await API.post(`/credentials/${id}/rotate`); Toast.success("Rotation complete"); this.load(); } catch(e) {} },
    async verify(id) { Toast.info("Verifying..."); try { const data = await API.post(`/credentials/${id}/verify`); Toast.success(`Verified: ${data.success_count || 0} OK, ${data.failure_count || 0} failed`); } catch(e) {} },
    rotateSelected() { Toast.info("Select a credential to rotate"); },
    verifyAll() { Toast.info("Use CLI: creds verify"); },
};

// ---------------------------------------------------------------------------
// Firewall Management
// ---------------------------------------------------------------------------
const Firewall = {
    currentTab: "rules",

    async load() {
        const select = document.getElementById("firewall-device-select");
        if (select && select.options.length <= 1) {
            try {
                const data = await API.get("/devices");
                (data.devices || []).forEach(d => {
                    const opt = document.createElement("option");
                    opt.value = d.id;
                    opt.textContent = d.hostname || d.ip_address || d.id;
                    select.appendChild(opt);
                });
            } catch (e) { /* no devices */ }
        }
    },

    switchTab(tab, btn) {
        this.currentTab = tab;
        document.querySelectorAll(".fw-tab").forEach(b => b.classList.remove("active"));
        if (btn) btn.classList.add("active");
        this.loadTab();
    },

    async loadTab() {
        const deviceId = document.getElementById("firewall-device-select")?.value;
        if (!deviceId) return;
        const content = document.getElementById("firewall-content");
        content.innerHTML = "<p>Loading...</p>";
        try {
            if (this.currentTab === "rules") await this.loadRules(deviceId);
            else if (this.currentTab === "nat") await this.loadNat(deviceId);
            else if (this.currentTab === "zones") await this.loadZones(deviceId);
            else if (this.currentTab === "objects") await this.loadObjects(deviceId);
        } catch (e) {
            content.innerHTML = `<p class="text-danger">Error: ${e.message}</p>`;
        }
    },

    async loadRules(deviceId) {
        const data = await API.get(`/firewall/rules?device_id=${deviceId}`);
        const rules = data.rules || [];
        const content = document.getElementById("firewall-content");
        if (!rules.length) { content.innerHTML = '<p>No rules found. Click "Sync from Device" to pull rules.</p>'; return; }
        let html = '<table class="data-table"><thead><tr><th>#</th><th>Name</th><th>Src Zone</th><th>Dst Zone</th><th>Src Addr</th><th>Dst Addr</th><th>Services</th><th>Action</th><th>Enabled</th><th>Log</th></tr></thead><tbody>';
        rules.forEach(r => {
            const srcA = Array.isArray(r.source_addresses) ? r.source_addresses : JSON.parse(r.source_addresses || "[]");
            const dstA = Array.isArray(r.dest_addresses) ? r.dest_addresses : JSON.parse(r.dest_addresses || "[]");
            const svcs = Array.isArray(r.services) ? r.services : JSON.parse(r.services || "[]");
            const cls = r.action === "allow" ? "text-success" : "text-danger";
            html += `<tr><td>${r.position || ""}</td><td>${r.name || ""}</td><td>${r.source_zone || ""}</td><td>${r.dest_zone || ""}</td><td>${srcA.join(", ") || "any"}</td><td>${dstA.join(", ") || "any"}</td><td>${svcs.join(", ") || "any"}</td><td class="${cls}">${r.action || ""}</td><td>${r.enabled ? "Yes" : "No"}</td><td>${r.log_enabled ? "Yes" : "No"}</td></tr>`;
        });
        html += "</tbody></table>";
        content.innerHTML = html;
    },

    async loadNat(deviceId) {
        const data = await API.get(`/firewall/nat?device_id=${deviceId}`);
        const rules = data.nat_rules || [];
        const content = document.getElementById("firewall-content");
        if (!rules.length) { content.innerHTML = "<p>No NAT rules found.</p>"; return; }
        let html = '<table class="data-table"><thead><tr><th>Name</th><th>Type</th><th>Src Zone</th><th>Dst Zone</th><th>Orig Src</th><th>Orig Dst</th><th>Trans Src</th><th>Trans Dst</th><th>Enabled</th></tr></thead><tbody>';
        rules.forEach(r => {
            html += `<tr><td>${r.name || ""}</td><td>${r.nat_type || ""}</td><td>${r.source_zone || ""}</td><td>${r.dest_zone || ""}</td><td>${r.original_source || ""}</td><td>${r.original_dest || ""}</td><td>${r.translated_source || ""}</td><td>${r.translated_dest || ""}</td><td>${r.enabled ? "Yes" : "No"}</td></tr>`;
        });
        html += "</tbody></table>";
        content.innerHTML = html;
    },

    async loadZones(deviceId) {
        const data = await API.get(`/firewall/zones?device_id=${deviceId}`);
        const zones = data.zones || [];
        const content = document.getElementById("firewall-content");
        if (!zones.length) { content.innerHTML = '<p>No zones found. Click "Sync from Device" to pull zones.</p>'; return; }
        let html = '<table class="data-table"><thead><tr><th>Name</th><th>Interfaces</th><th>Security Level</th><th>Description</th></tr></thead><tbody>';
        zones.forEach(z => {
            const ifaces = Array.isArray(z.interfaces) ? z.interfaces : JSON.parse(z.interfaces || "[]");
            html += `<tr><td>${z.name || ""}</td><td>${ifaces.join(", ") || "-"}</td><td>${z.security_level || 0}</td><td>${z.description || "-"}</td></tr>`;
        });
        html += "</tbody></table>";
        content.innerHTML = html;
    },

    async loadObjects(deviceId) {
        const data = await API.get(`/firewall/objects?device_id=${deviceId}`);
        const objects = data.objects || [];
        const content = document.getElementById("firewall-content");
        if (!objects.length) { content.innerHTML = "<p>No objects found.</p>"; return; }
        let html = '<table class="data-table"><thead><tr><th>Name</th><th>Type</th><th>Value</th><th>Members</th><th>Description</th></tr></thead><tbody>';
        objects.forEach(o => {
            const members = Array.isArray(o.members) ? o.members : JSON.parse(o.members || "[]");
            html += `<tr><td>${o.name || ""}</td><td><span class="badge">${o.object_type || ""}</span></td><td>${o.value || "-"}</td><td>${members.join(", ") || "-"}</td><td>${o.description || "-"}</td></tr>`;
        });
        html += "</tbody></table>";
        content.innerHTML = html;
    },

    async syncRules() {
        const deviceId = document.getElementById("firewall-device-select")?.value;
        if (!deviceId) { Toast.warn("Select a device first"); return; }
        Toast.info("Syncing firewall rules...");
        try {
            await API.post(`/firewall/sync/${deviceId}`);
            Toast.success("Sync complete");
            this.loadTab();
        } catch (e) { Toast.error("Sync failed: " + e.message); }
    },
};
