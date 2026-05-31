/* ============================================================
   NetworkAgent Dashboard -- Device Management View
   ============================================================ */

"use strict";

const Devices = {
    editingId: null,

    async load() {
        await this.loadDevices();
        this.bindDeviceEvents();
    },

    // ---- Load & Render ----

    async loadDevices() {
        const statusFilter = $("device-filter-status") ? $("device-filter-status").value : "";
        let url = "/devices";
        const params = [];
        if (statusFilter) params.push(`status_filter=${statusFilter}`);
        if (params.length) url += "?" + params.join("&");

        const data = await API.get(url);
        AppState.devices = data.devices || [];
        this.renderTable(AppState.devices);
    },

    renderTable(devices) {
        const searchTerm = ($("device-search") ? $("device-search").value : "").toLowerCase();
        const filtered = devices.filter(d => {
            if (!searchTerm) return true;
            const text = [d.hostname, d.ip_address, d.device_type, d.vendor, d.status]
                .filter(Boolean).join(" ").toLowerCase();
            return text.includes(searchTerm);
        });

        const tbody = $("devices-table").querySelector("tbody");
        tbody.innerHTML = "";

        if (filtered.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">No devices found</td></tr>';
            return;
        }

        filtered.forEach(d => {
            const id = d.id || d.device_id;
            const row = document.createElement("tr");
            row.style.cursor = "pointer";
            row.innerHTML = `
                <td>${escapeHTML(d.hostname || "--")}</td>
                <td>${escapeHTML(d.ip_address || "--")}</td>
                <td>${escapeHTML(d.device_type || "--")}</td>
                <td>${escapeHTML(d.vendor || "--")}</td>
                <td>${badgeHTML(d.status || "unknown")}</td>
                <td class="action-cell">
                    <button class="btn btn-sm" onclick="event.stopPropagation(); Devices.showDetail('${id}')">Details</button>
                    <button class="btn btn-sm" onclick="event.stopPropagation(); Devices.testDevice('${id}')">Test</button>
                    <button class="btn btn-sm" onclick="event.stopPropagation(); Devices.editDevice('${id}')">Edit</button>
                    <button class="btn btn-sm btn-danger" onclick="event.stopPropagation(); Devices.deleteDevice('${id}')">Del</button>
                </td>`;
            row.addEventListener("click", () => Devices.showDetail(id));
            tbody.appendChild(row);
        });
    },

    // ---- Bind Events (called once) ----

    _bound: false,
    bindDeviceEvents() {
        if (this._bound) return;
        this._bound = true;

        // Search
        $("device-search").addEventListener("input", () => {
            this.renderTable(AppState.devices);
        });

        // Status filter
        $("device-filter-status").addEventListener("change", () => {
            this.loadDevices();
        });

        // Add device button
        $("btn-add-device").addEventListener("click", () => {
            this.editingId = null;
            $("device-modal-title").textContent = "Add Device";
            $("device-form").reset();
            $("df-id").value = "";
            $("device-modal").classList.remove("hidden");
        });

        // Modal close
        $("device-modal-close").addEventListener("click", () => {
            $("device-modal").classList.add("hidden");
        });
        $("btn-cancel-device").addEventListener("click", () => {
            $("device-modal").classList.add("hidden");
        });

        // Form submit
        $("device-form").addEventListener("submit", async (e) => {
            e.preventDefault();
            await this.saveDevice();
        });

        // Detail panel close
        $("detail-close").addEventListener("click", () => {
            $("device-detail-panel").classList.add("hidden");
        });
    },

    // ---- CRUD Operations ----

    async saveDevice() {
        const payload = {
            hostname:      $("df-hostname").value.trim(),
            ip_address:    $("df-ip").value.trim(),
            device_type:   $("df-type").value,
            vendor:        $("df-vendor").value.trim() || undefined,
            model:         $("df-model").value.trim() || undefined,
            location:      $("df-location").value.trim() || undefined,
            credential_id: $("df-cred").value.trim() || undefined,
        };

        if (this.editingId) {
            await API.put(`/devices/${this.editingId}`, payload);
            Toast.success("Device updated");
        } else {
            await API.post("/devices", payload);
            Toast.success("Device added");
        }

        $("device-modal").classList.add("hidden");
        await this.loadDevices();
    },

    async editDevice(deviceId) {
        const device = await API.get(`/devices/${deviceId}`);
        this.editingId = deviceId;
        $("device-modal-title").textContent = "Edit Device";
        $("df-id").value       = deviceId;
        $("df-hostname").value = device.hostname || "";
        $("df-ip").value       = device.ip_address || "";
        $("df-type").value     = device.device_type || "router";
        $("df-vendor").value   = device.vendor || "";
        $("df-model").value    = device.model || "";
        $("df-location").value = device.location || "";
        $("df-cred").value     = device.credential_id || "";
        $("device-modal").classList.remove("hidden");
    },

    async deleteDevice(deviceId) {
        if (!confirm("Delete this device? This action cannot be undone.")) return;
        await API.del(`/devices/${deviceId}`);
        Toast.success("Device deleted");
        $("device-detail-panel").classList.add("hidden");
        await this.loadDevices();
    },

    async testDevice(deviceId) {
        Toast.info("Testing connectivity...");
        const result = await API.post(`/devices/${deviceId}/test`);
        if (result.success) {
            Toast.success("Connectivity OK");
        } else {
            Toast.error(`Test failed: ${result.error || "Unknown error"}`);
        }
    },

    // ---- Detail Panel ----

    async showDetail(deviceId) {
        const device = await API.get(`/devices/${deviceId}`);
        $("detail-hostname").textContent = device.hostname || device.ip_address || deviceId;

        const body = $("detail-body");
        const fields = [
            ["ID",            device.id || device.device_id],
            ["Hostname",      device.hostname],
            ["IP Address",    device.ip_address],
            ["Type",          device.device_type],
            ["Vendor",        device.vendor],
            ["Model",         device.model],
            ["OS Version",    device.os_version],
            ["Serial Number", device.serial_number],
            ["Location",      device.location],
            ["Status",        device.status],
            ["Last Seen",     formatDate(device.last_seen)],
            ["Tags",          (device.tags || []).join(", ") || "None"],
        ];

        body.innerHTML = fields.map(([label, value]) =>
            `<div class="detail-row">
                <span class="label">${label}</span>
                <span class="value">${escapeHTML(String(value || "--"))}</span>
            </div>`
        ).join("");

        // Action buttons
        body.innerHTML += `
            <div style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap">
                <button class="btn btn-sm btn-primary" onclick="Devices.testDevice('${deviceId}')">Test Connectivity</button>
                <button class="btn btn-sm" onclick="Devices.editDevice('${deviceId}')">Edit</button>
                <button class="btn btn-sm btn-danger" onclick="Devices.deleteDevice('${deviceId}')">Delete</button>
            </div>
            <div style="margin-top:16px">
                <h4 style="color:var(--text-heading);font-size:0.9rem;margin-bottom:8px">Tags</h4>
                <div id="detail-tags" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
                    ${(device.tags || []).map(t => `
                        <span class="badge badge-info" style="cursor:pointer" title="Click to remove"
                              onclick="Devices.removeTag('${deviceId}','${t}')">${escapeHTML(t)} &times;</span>
                    `).join("")}
                </div>
                <div style="display:flex;gap:6px">
                    <input type="text" class="input" id="detail-tag-input" placeholder="New tag" style="max-width:160px">
                    <button class="btn btn-sm btn-primary" onclick="Devices.addTag('${deviceId}')">Add</button>
                </div>
            </div>`;

        $("device-detail-panel").classList.remove("hidden");
    },

    // ---- Tags ----

    async addTag(deviceId) {
        const input = $("detail-tag-input");
        const tag = input.value.trim();
        if (!tag) return;
        await API.post(`/devices/${deviceId}/tags`, { tag });
        Toast.success(`Tag "${tag}" added`);
        input.value = "";
        this.showDetail(deviceId);
        this.loadDevices();
    },

    async removeTag(deviceId, tag) {
        await API.del(`/devices/${deviceId}/tags/${encodeURIComponent(tag)}`);
        Toast.success(`Tag "${tag}" removed`);
        this.showDetail(deviceId);
        this.loadDevices();
    },
};
