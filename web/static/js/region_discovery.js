// region_discovery.js — список кандидатов на сообщества для региона.

(function () {
    'use strict';

    const REGION_CODE = window.REGION_CODE;
    const CATEGORIES = ['admin', 'novost', 'reklama', 'sosed', 'kultura', 'sport', 'detsad'];

    const tbody = document.getElementById('candidates-tbody');
    const statusMsg = document.getElementById('status-msg');
    const regionInfo = document.getElementById('region-info');
    const countInfo = document.getElementById('count-info');
    const rerunBtn = document.getElementById('rerun-btn');

    const filterStatus = document.getElementById('filter-status');
    const filterConfidence = document.getElementById('filter-confidence');
    const filterInfoOnly = document.getElementById('filter-info-only');
    const bulkApproveBtn = document.getElementById('bulk-approve');
    const bulkRejectBtn = document.getElementById('bulk-reject');

    const approveModalEl = document.getElementById('approve-modal');
    const approveModal = new bootstrap.Modal(approveModalEl);
    const approveModalName = document.getElementById('approve-modal-name');
    const approveModalReasoning = document.getElementById('approve-modal-reasoning');
    const approveModalCategory = document.getElementById('approve-modal-category');
    const approveModalConfirm = document.getElementById('approve-modal-confirm');

    let region = null;
    let candidates = [];

    init();

    async function init() {
        try {
            // Find region by code.
            const regResp = await fetch(`/api/regions/`);
            const regs = await regResp.json();
            const list = Array.isArray(regs) ? regs : (regs.regions || []);
            region = list.find(r => r.code === REGION_CODE);
            if (!region) {
                regionInfo.textContent = `Регион «${REGION_CODE}» не найден`;
                tbody.innerHTML = '';
                return;
            }
            regionInfo.innerHTML = `<strong>${escapeHtml(region.name)}</strong>
                · центр: <code>${escapeHtml(region.center_city || '—')}</code>
                · vk_city_id: <code>${region.vk_city_id || '—'}</code>`;
            await load();
        } catch (e) {
            regionInfo.innerHTML = `<span class="text-danger">Ошибка инициализации: ${escapeHtml(e.message)}</span>`;
        }

        filterStatus.addEventListener('change', load);
        filterConfidence.addEventListener('change', load);
        filterInfoOnly.addEventListener('change', load);
        rerunBtn.addEventListener('click', rerunDiscovery);
        bulkApproveBtn.addEventListener('click', bulkApprove);
        bulkRejectBtn.addEventListener('click', bulkReject);
    }

    async function load() {
        const params = new URLSearchParams({region_id: region.id});
        if (filterStatus.value) params.set('status', filterStatus.value);
        if (parseInt(filterConfidence.value, 10) > 0) params.set('min_confidence', filterConfidence.value);
        if (filterInfoOnly.checked) params.set('only_info_pages', '1');

        tbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center py-3">Загружаю…</td></tr>';
        try {
            const resp = await fetch(`/api/discovery/candidates?${params.toString()}`);
            const data = await resp.json();
            candidates = data.candidates || [];
            renderTable();
            countInfo.textContent = `Показано: ${data.count}`;
            // Bulk-кнопки активны только когда есть pending'и.
            const hasPending = candidates.some(c => c.status === 'pending');
            bulkApproveBtn.disabled = !hasPending;
            bulkRejectBtn.disabled = !hasPending;
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-danger text-center">Ошибка: ${escapeHtml(e.message)}</td></tr>`;
        }
    }

    function renderTable() {
        if (!candidates.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center py-3">Кандидатов нет.</td></tr>';
            return;
        }
        tbody.innerHTML = candidates.map(c => {
            const photo = c.photo_url
                ? `<img src="${c.photo_url}" alt="" width="40" height="40" class="rounded">`
                : `<div class="rounded bg-light" style="width:40px;height:40px;"></div>`;
            const vkLink = `https://vk.com/${c.screen_name || ('club' + c.vk_id)}`;
            const infoBadge = c.ai_is_info_page
                ? '<span class="badge bg-warning text-dark ms-1" title="AI считает это ИНФО-страницей">ИНФО</span>'
                : '';
            const confidenceBar = c.ai_confidence != null
                ? `<div class="progress" style="height:6px;">
                       <div class="progress-bar bg-${confidenceColor(c.ai_confidence)}"
                            style="width:${c.ai_confidence}%"></div>
                   </div>
                   <small class="text-muted">${c.ai_confidence}%</small>`
                : '<small class="text-muted">—</small>';
            const cat = c.ai_category || '—';
            const statusBadge = c.status === 'pending'
                ? '' : `<span class="badge bg-secondary ms-2">${escapeHtml(c.status)}</span>`;
            const actions = c.status === 'pending'
                ? `<button class="btn btn-sm btn-outline-success" data-act="approve" data-id="${c.id}">
                       <i class="bi bi-check2"></i> Approve
                   </button>
                   <button class="btn btn-sm btn-outline-danger" data-act="reject" data-id="${c.id}">
                       <i class="bi bi-x"></i> Reject
                   </button>
                   <button class="btn btn-sm btn-outline-secondary" data-act="defer" data-id="${c.id}">
                       <i class="bi bi-pause"></i> Defer
                   </button>`
                : `<button class="btn btn-sm btn-link text-muted" disabled>${escapeHtml(c.status)}</button>`;
            return `<tr data-id="${c.id}">
                <td>${photo}</td>
                <td>
                    <a href="${vkLink}" target="_blank" class="fw-bold">${escapeHtml(c.name)}</a>
                    ${infoBadge}${statusBadge}<br>
                    <small class="text-muted">${escapeHtml(c.description || '')}</small>
                </td>
                <td class="text-end">${(c.members_count ?? '—').toLocaleString('ru-RU')}</td>
                <td>
                    <span class="badge bg-${categoryColor(cat)}">${escapeHtml(cat)}</span>
                    ${confidenceBar}
                </td>
                <td><small>${escapeHtml(c.ai_reasoning || '')}</small></td>
                <td class="text-end">${actions}</td>
            </tr>`;
        }).join('');

        tbody.querySelectorAll('button[data-act]').forEach(btn => {
            btn.addEventListener('click', () => onAction(btn.dataset.act, parseInt(btn.dataset.id, 10)));
        });
    }

    function onAction(act, id) {
        const cand = candidates.find(c => c.id === id);
        if (!cand) return;
        if (act === 'approve') return showApproveModal(cand);
        if (act === 'reject') return patchCandidate(id, {status: 'rejected'});
        if (act === 'defer') return patchCandidate(id, {status: 'deferred'});
    }

    function showApproveModal(cand) {
        approveModalName.textContent = cand.name;
        approveModalReasoning.textContent = cand.ai_reasoning || '';
        const options = CATEGORIES.map(c => {
            const sel = c === cand.ai_category ? 'selected' : '';
            return `<option value="${c}" ${sel}>${c}</option>`;
        }).join('');
        approveModalCategory.innerHTML = options;
        approveModalConfirm.onclick = async () => {
            const cat = approveModalCategory.value;
            approveModal.hide();
            await patchCandidate(cand.id, {status: 'approved', category: cat});
        };
        approveModal.show();
    }

    async function patchCandidate(id, body) {
        setStatus('info', '⏳ Сохраняю…');
        try {
            const resp = await fetch(`/api/discovery/candidates/${id}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            setStatus('success', `✓ ${body.status}`);
            await load();
        } catch (e) {
            setStatus('danger', `Ошибка: ${e.message}`);
        }
    }

    async function rerunDiscovery() {
        if (!confirm('Перезапустить discovery? Может занять до минуты.')) return;
        rerunBtn.disabled = true;
        rerunBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Идёт…';
        try {
            const resp = await fetch('/api/discovery/trigger', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({region_id: region.id}),
            });
            const r = await resp.json();
            if (!resp.ok) throw new Error(r.detail || `HTTP ${resp.status}`);
            setStatus('success',
                `Найдено ${r.found}, новых ${r.inserted}, обновлено ${r.refreshed}.`);
            await load();
        } catch (e) {
            setStatus('danger', `Ошибка: ${e.message}`);
        } finally {
            rerunBtn.disabled = false;
            rerunBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Перезапустить discovery';
        }
    }

    async function bulkApprove() {
        const conf = parseInt(filterConfidence.value, 10) || 70;
        if (!confirm(`Approve всех pending с confidence ≥ ${conf} и ai_category ≠ other/null?`)) return;
        setStatus('info', '⏳ Массовый approve…');
        try {
            const resp = await fetch('/api/discovery/candidates/bulk', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    region_id: region.id,
                    status: 'approved',
                    min_confidence: conf,
                    only_info_pages: filterInfoOnly.checked,
                }),
            });
            const r = await resp.json();
            if (!resp.ok) throw new Error(r.detail || `HTTP ${resp.status}`);
            setStatus('success',
                `Одобрено: ${r.approved}, пропущено без категории: ${r.skipped_no_category}`);
            await load();
        } catch (e) {
            setStatus('danger', `Ошибка: ${e.message}`);
        }
    }

    async function bulkReject() {
        const conf = parseInt(filterConfidence.value, 10) || 0;
        if (!confirm(`Reject всех pending${conf ? ` с confidence ≥ ${conf}` : ''}?`)) return;
        setStatus('info', '⏳ Массовый reject…');
        try {
            const resp = await fetch('/api/discovery/candidates/bulk', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    region_id: region.id,
                    status: 'rejected',
                    min_confidence: conf > 0 ? conf : null,
                }),
            });
            const r = await resp.json();
            if (!resp.ok) throw new Error(r.detail || `HTTP ${resp.status}`);
            setStatus('success', `Отклонено: ${r.updated}`);
            await load();
        } catch (e) {
            setStatus('danger', `Ошибка: ${e.message}`);
        }
    }

    function setStatus(level, html) {
        statusMsg.innerHTML = `<div class="alert alert-${level} py-2">${html}</div>`;
        if (level === 'success') {
            setTimeout(() => { statusMsg.innerHTML = ''; }, 4000);
        }
    }

    function confidenceColor(c) {
        if (c >= 80) return 'success';
        if (c >= 50) return 'warning';
        return 'secondary';
    }

    function categoryColor(cat) {
        const map = {admin: 'primary', novost: 'info', reklama: 'warning',
                     sosed: 'secondary', kultura: 'success', sport: 'success',
                     detsad: 'info', other: 'light text-dark'};
        return map[cat] || 'secondary';
    }

    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }
})();
