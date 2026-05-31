/**
 * Automation view controller for the Network Agent dashboard.
 */
const Automation = {
    _bound: false,

    async load() {
        await Promise.all([
            this.loadRunbooks(),
            this.loadExecutions(),
            this.loadJobs(),
            this.loadAuditLog(),
        ]);
        if (!this._bound) {
            this.bindEvents();
            this._bound = true;
        }
    },

    async loadRunbooks() {
        try {
            const data = await API.get('/automation/runbooks');
            const tbody = document.getElementById('automation-runbooks-body');
            if (!tbody) return;
            const counter = document.getElementById('auto-runbook-count');
            if (counter) counter.textContent = data.length;

            if (!data.length) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;opacity:.5">No runbooks configured</td></tr>';
                return;
            }
            tbody.innerHTML = data.map(rb => {
                const statusBadge = rb.enabled
                    ? '<span class="badge badge-online">Enabled</span>'
                    : '<span class="badge badge-offline">Disabled</span>';
                const triggerBadge = {
                    alert: '<span class="badge badge-warning">Alert</span>',
                    schedule: '<span class="badge badge-info">Schedule</span>',
                    webhook: '<span class="badge badge-info">Webhook</span>',
                    manual: '<span class="badge">Manual</span>',
                }[rb.trigger_type] || `<span class="badge">${escapeHTML(rb.trigger_type)}</span>`;

                return `<tr>
                    <td><strong>${escapeHTML(rb.name)}</strong></td>
                    <td>${triggerBadge}</td>
                    <td>${statusBadge}</td>
                    <td>${rb.actions_count}</td>
                    <td>${escapeHTML(rb.description || '')}</td>
                    <td>
                        <button class="btn btn-sm" onclick="Automation.executeRunbook('${escapeHTML(rb.name)}')">Run</button>
                        ${rb.enabled
                            ? `<button class="btn btn-sm" onclick="Automation.toggleRunbook('${escapeHTML(rb.name)}', false)">Disable</button>`
                            : `<button class="btn btn-sm" onclick="Automation.toggleRunbook('${escapeHTML(rb.name)}', true)">Enable</button>`
                        }
                        <button class="btn btn-sm btn-danger" onclick="Automation.deleteRunbook('${escapeHTML(rb.name)}')">Del</button>
                    </td>
                </tr>`;
            }).join('');
        } catch (e) {
            console.error('Failed to load runbooks:', e);
        }
    },

    async loadExecutions() {
        try {
            const data = await API.get('/automation/executions?limit=20');
            const tbody = document.getElementById('automation-executions-body');
            if (!tbody) return;
            const counter = document.getElementById('auto-exec-count');
            if (counter) counter.textContent = data.length;

            if (!data.length) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;opacity:.5">No executions yet</td></tr>';
                return;
            }
            tbody.innerHTML = data.map(ex => {
                const statusMap = {
                    completed: 'badge-online',
                    running: 'badge-warning',
                    pending: 'badge-info',
                    failed: 'badge-offline',
                    escalated: 'badge-critical',
                    cancelled: 'badge-degraded',
                };
                const cls = statusMap[ex.status] || '';
                const duration = ex.duration_seconds
                    ? `${Number(ex.duration_seconds).toFixed(1)}s`
                    : '-';
                return `<tr>
                    <td title="${escapeHTML(ex.id)}">${escapeHTML((ex.id || '').slice(0, 8))}</td>
                    <td>${escapeHTML(ex.runbook_name || '')}</td>
                    <td>${escapeHTML(ex.trigger_type || '')}</td>
                    <td>${escapeHTML(ex.device_id || '-')}</td>
                    <td><span class="badge ${cls}">${escapeHTML(ex.status || '')}</span></td>
                    <td>${duration}</td>
                    <td>${formatDate(ex.started_at)}</td>
                </tr>`;
            }).join('');
        } catch (e) {
            console.error('Failed to load executions:', e);
        }
    },

    async loadJobs() {
        try {
            const data = await API.get('/automation/jobs');
            const tbody = document.getElementById('automation-jobs-body');
            if (!tbody) return;
            const counter = document.getElementById('auto-job-count');
            if (counter) counter.textContent = data.length;

            if (!data.length) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;opacity:.5">No scheduled jobs</td></tr>';
                return;
            }
            tbody.innerHTML = data.map(job => {
                const typeBadge = job.type === 'builtin'
                    ? '<span class="badge badge-info">Built-in</span>'
                    : '<span class="badge badge-warning">Runbook</span>';
                const statusBadge = job.enabled
                    ? '<span class="badge badge-online">Active</span>'
                    : '<span class="badge badge-offline">Paused</span>';
                const nextRun = job.next_run ? formatDate(job.next_run) : '-';

                return `<tr>
                    <td>${escapeHTML(job.name || job.id)}</td>
                    <td>${typeBadge}</td>
                    <td><code>${escapeHTML(job.cron || '')}</code></td>
                    <td>${nextRun}</td>
                    <td>${statusBadge}</td>
                    <td>
                        <button class="btn btn-sm" onclick="Automation.runJob('${escapeHTML(job.id)}')">Run Now</button>
                        ${job.enabled
                            ? `<button class="btn btn-sm" onclick="Automation.toggleJob('${escapeHTML(job.id)}', true)">Pause</button>`
                            : `<button class="btn btn-sm" onclick="Automation.toggleJob('${escapeHTML(job.id)}', false)">Resume</button>`
                        }
                    </td>
                </tr>`;
            }).join('');
        } catch (e) {
            console.error('Failed to load jobs:', e);
        }
    },

    async loadAuditLog() {
        try {
            const data = await API.get('/audit/?limit=15');
            const tbody = document.getElementById('automation-audit-body');
            if (!tbody) return;

            if (!data.length) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;opacity:.5">No audit entries</td></tr>';
                return;
            }
            tbody.innerHTML = data.map(entry => {
                const resultBadge = entry.result === 'success'
                    ? '<span class="badge badge-online">Success</span>'
                    : entry.result === 'failure'
                    ? '<span class="badge badge-offline">Failed</span>'
                    : `<span class="badge">${escapeHTML(entry.result || '-')}</span>`;
                return `<tr>
                    <td>${formatDate(entry.timestamp)}</td>
                    <td>${escapeHTML(entry.actor || '')}</td>
                    <td>${escapeHTML(entry.action_type || '')}</td>
                    <td>${escapeHTML(entry.device_id || '-')}</td>
                    <td title="${escapeHTML(entry.description || '')}">${escapeHTML((entry.description || '').slice(0, 80))}</td>
                    <td>${resultBadge}</td>
                </tr>`;
            }).join('');
        } catch (e) {
            console.error('Failed to load audit log:', e);
        }
    },

    async executeRunbook(name) {
        if (!confirm(`Execute runbook "${name}"?`)) return;
        try {
            const result = await API.post(`/automation/runbooks/${encodeURIComponent(name)}/execute`);
            if (result) {
                Toast.success(`Runbook "${name}" executing (ID: ${(result.execution_id || '').slice(0, 8)})`);
                setTimeout(() => {
                    this.loadExecutions();
                    this.loadAuditLog();
                }, 2000);
            }
        } catch (e) {
            Toast.error(`Failed to execute runbook: ${e.message || e}`);
        }
    },

    async toggleRunbook(name, enable) {
        const action = enable ? 'enable' : 'disable';
        try {
            const result = await API.post(`/automation/runbooks/${encodeURIComponent(name)}/${action}`);
            if (result) {
                Toast.success(`Runbook "${name}" ${action}d`);
                this.loadRunbooks();
            }
        } catch (e) {
            Toast.error(`Failed to ${action} runbook: ${e.message || e}`);
        }
    },

    async deleteRunbook(name) {
        if (!confirm(`Delete runbook "${name}"? This cannot be undone.`)) return;
        try {
            await API.del(`/automation/runbooks/${encodeURIComponent(name)}`);
            Toast.success(`Runbook "${name}" deleted`);
            this.loadRunbooks();
        } catch (e) {
            Toast.error(`Failed to delete runbook: ${e.message || e}`);
        }
    },

    async reloadRunbooks() {
        try {
            const result = await API.post('/automation/runbooks/reload');
            if (result) {
                Toast.success(`Reloaded ${result.count} runbook(s) from disk`);
                this.loadRunbooks();
            }
        } catch (e) {
            Toast.error(`Failed to reload: ${e.message || e}`);
        }
    },

    async runJob(jobId) {
        if (!confirm(`Run job "${jobId}" now?`)) return;
        try {
            await API.post(`/automation/jobs/${encodeURIComponent(jobId)}/run`);
            Toast.success(`Job "${jobId}" triggered`);
            setTimeout(() => this.loadAuditLog(), 3000);
        } catch (e) {
            Toast.error(`Failed to run job: ${e.message || e}`);
        }
    },

    async toggleJob(jobId, pause) {
        const action = pause ? 'pause' : 'resume';
        try {
            await API.post(`/automation/jobs/${encodeURIComponent(jobId)}/${action}`);
            Toast.success(`Job "${jobId}" ${action}d`);
            this.loadJobs();
        } catch (e) {
            Toast.error(`Failed to ${action} job: ${e.message || e}`);
        }
    },

    bindEvents() {
        // Auto-refresh every 30 seconds when on automation view
        setInterval(() => {
            const view = document.getElementById('view-automation');
            if (view && view.classList.contains('active')) {
                this.loadExecutions();
                this.loadAuditLog();
            }
        }, 30000);
    }
};
