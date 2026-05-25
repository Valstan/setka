// region_discovery.js — кандидаты сообществ для региона, сгруппированы по тематикам.
//
// Ключевые отличия от MVP (PR #31):
//   - Группировка по AI-категориям (секции с заголовками + счётчиками).
//   - Inline `<select>` категории в каждой карточке. При смене →
//     PATCH /api/discovery/candidates/{id} {category: ...} и DOM-перемещение
//     карточки в новую секцию без полного reload.
//   - Финальная кнопка "Создать регион в Сетке" → POST /api/discovery/commit/{region_id}.
//   - Approve/Reject/Defer на каждом кандидате; bulk reject; rerun discovery.

(function () {
    'use strict';

    const REGION_CODE = window.REGION_CODE;
    const CATEGORIES = ['admin', 'novost', 'reklama', 'sosed', 'kultura', 'sport', 'detsad', 'other'];
    const CATEGORY_LABELS = {
        admin: 'Администрация и госорганы',
        novost: 'Новости района',
        reklama: 'Объявления и барахолка',
        sosed: 'Соседи, ДТП, происшествия',
        kultura: 'Культура и афиша',
        sport: 'Спорт и фитнес',
        detsad: 'Детский сад и школа',
        other: 'Другое (требует ручной категории)',
        _none: 'Без AI-категории',
    };
    const SECTION_ORDER = [...CATEGORIES, '_none'];

    const sectionsContainer = document.getElementById('sections-container');
    const statusMsg = document.getElementById('status-msg');
    const regionInfo = document.getElementById('region-info');
    const countInfo = document.getElementById('count-info');
    const rerunBtn = document.getElementById('rerun-btn');
    const commitBtn = document.getElementById('commit-btn');

    const filterStatus = document.getElementById('filter-status');
    const filterConfidence = document.getElementById('filter-confidence');
    const filterInfoOnly = document.getElementById('filter-info-only');
    const filterHideIrrelevant = document.getElementById('filter-hide-irrelevant');
    const bulkRejectBtn = document.getElementById('bulk-reject');

    let region = null;
    let candidates = [];

    init();

    async function init() {
        try {
            const regResp = await fetch('/api/regions/');
            const regs = await regResp.json();
            const list = Array.isArray(regs) ? regs : (regs.regions || []);
            region = list.find(r => r.code === REGION_CODE);
            if (!region) {
                regionInfo.textContent = `Регион «${REGION_CODE}» не найден`;
                sectionsContainer.innerHTML = '';
                return;
            }
            regionInfo.innerHTML = renderRegionHeader(region);
            await load();
        } catch (e) {
            regionInfo.innerHTML = `<span class="text-danger">Ошибка инициализации: ${escapeHtml(e.message)}</span>`;
        }

        filterStatus.addEventListener('change', load);
        filterConfidence.addEventListener('change', load);
        filterInfoOnly.addEventListener('change', load);
        filterHideIrrelevant.addEventListener('change', () => renderSections());
        rerunBtn.addEventListener('click', rerunDiscovery);
        bulkRejectBtn.addEventListener('click', bulkReject);
        commitBtn.addEventListener('click', commitRegion);
    }

    function renderRegionHeader(r) {
        const status = r.is_active
            ? '<span class="badge bg-success">активен</span>'
            : '<span class="badge bg-warning text-dark">черновик</span>';
        const vkGroup = r.vk_group_id
            ? `vk_group_id: <code>${r.vk_group_id}</code>`
            : '<span class="text-danger">⚠ нет главной группы</span>';
        return `<strong>${escapeHtml(r.name)}</strong> ${status} · ${vkGroup}`;
    }

    async function load() {
        const params = new URLSearchParams({region_id: region.id});
        if (filterStatus.value) params.set('status', filterStatus.value);
        if (parseInt(filterConfidence.value, 10) > 0) params.set('min_confidence', filterConfidence.value);
        if (filterInfoOnly.checked) params.set('only_info_pages', '1');

        sectionsContainer.innerHTML = '<div class="text-muted text-center py-4">Загружаю…</div>';
        try {
            const resp = await fetch(`/api/discovery/candidates?${params.toString()}`);
            const data = await resp.json();
            candidates = data.candidates || [];
            renderSections();
            countInfo.textContent = `Показано: ${data.count}`;

            const hasPending = candidates.some(c => c.status === 'pending');
            bulkRejectBtn.disabled = !hasPending;
            // commit активен только если есть хотя бы один pending кандидат с конкретной категорией
            const hasCommitable = candidates.some(
                c => c.status === 'pending' && c.ai_category && c.ai_category !== 'other'
            );
            commitBtn.disabled = !(hasCommitable && region && region.vk_group_id);
            commitBtn.title = !region.vk_group_id
                ? 'У региона нет главной VK-группы — финализация невозможна'
                : !hasCommitable
                    ? 'Распределите хотя бы одного кандидата по конкретной тематике'
                    : 'Создать регион в Сетке (активировать + bulk-approve выбранных)';
        } catch (e) {
            sectionsContainer.innerHTML = `<div class="text-danger text-center">Ошибка: ${escapeHtml(e.message)}</div>`;
        }
    }

    function groupBySectionKey(list) {
        const groups = {};
        for (const k of SECTION_ORDER) groups[k] = [];
        for (const c of list) {
            const key = c.ai_category && CATEGORIES.includes(c.ai_category) ? c.ai_category : '_none';
            groups[key].push(c);
        }
        return groups;
    }

    function renderSections() {
        if (!candidates.length) {
            sectionsContainer.innerHTML = '<div class="text-muted text-center py-3">Кандидатов нет.</div>';
            countInfo.textContent = '';
            return;
        }
        // Client-side фильтр «скрыть нерелевантных»: убираем тех, где
        // AI явно сказал is_relevant=false. NULL (не оценено) — оставляем.
        const visible = filterHideIrrelevant && filterHideIrrelevant.checked
            ? candidates.filter(c => c.ai_is_relevant !== false)
            : candidates;
        if (!visible.length) {
            sectionsContainer.innerHTML =
                '<div class="text-muted text-center py-3">Все кандидаты помечены AI как нерелевантные. ' +
                'Сними галочку «скрыть нерелевантных», чтобы увидеть их.</div>';
            countInfo.textContent = `Скрыто нерелевантных: ${candidates.length}`;
            return;
        }
        const hidden = candidates.length - visible.length;
        countInfo.textContent = `Показано: ${visible.length}` +
            (hidden ? ` · скрыто нерелевантных: ${hidden}` : '');
        const groups = groupBySectionKey(visible);
        const html = SECTION_ORDER.map(key => renderSection(key, groups[key])).join('');
        sectionsContainer.innerHTML = html;
        bindCardEventListeners();
    }

    function renderSection(key, items) {
        if (!items.length) return '';
        const headerColor = sectionHeaderColor(key);
        const sectionId = `section-${key}`;
        return `
            <div class="thematic-section mb-4" data-section-key="${key}">
                <h4 class="mb-2">
                    <span class="badge ${headerColor} me-2">${escapeHtml(CATEGORY_LABELS[key] || key)}</span>
                    <small class="text-muted">${items.length}</small>
                </h4>
                <div class="row g-2" id="${sectionId}">
                    ${items.map(renderCard).join('')}
                </div>
            </div>`;
    }

    function renderCard(c) {
        const photo = c.photo_url
            ? `<img src="${escapeAttr(c.photo_url)}" alt="" width="56" height="56" class="rounded">`
            : `<div class="rounded bg-light" style="width:56px;height:56px;"></div>`;
        const vkLink = `https://vk.com/${c.screen_name || ('club' + c.vk_id)}`;
        const infoBadge = c.ai_is_info_page
            ? '<span class="badge bg-warning text-dark ms-1" title="AI считает это ИНФО-страницей">ИНФО</span>'
            : '';
        let relevanceBadge = '';
        if (c.ai_is_relevant === true) {
            relevanceBadge = '<span class="badge bg-success ms-1" title="AI: принадлежит району">✓ районный</span>';
        } else if (c.ai_is_relevant === false) {
            relevanceBadge = '<span class="badge bg-secondary ms-1" title="AI: нерелевантен району">✗ не районный</span>';
        }
        const confidence = c.ai_confidence != null
            ? `<small class="text-muted">AI: ${c.ai_confidence}%</small>`
            : '<small class="text-muted">AI: —</small>';
        const statusBadge = c.status === 'pending'
            ? '' : `<span class="badge bg-secondary ms-2">${escapeHtml(c.status)}</span>`;
        const members = (c.members_count != null)
            ? `${(c.members_count).toLocaleString('ru-RU')} подписчиков` : '—';

        const catOptions = [...CATEGORIES, '_none'].map(cat => {
            const value = cat === '_none' ? '' : cat;
            const sel = (c.ai_category || '_none') === cat || (cat === '_none' && !c.ai_category)
                ? 'selected' : '';
            return `<option value="${value}" ${sel}>${escapeHtml(CATEGORY_LABELS[cat] || cat)}</option>`;
        }).join('');

        // pending: approve / reject / defer / delete; non-pending: только delete
        const baseButtons = c.status === 'pending'
            ? `<button class="btn btn-outline-success" data-act="approve" data-id="${c.id}" title="Approve с текущей категорией">
                   <i class="bi bi-check2"></i>
               </button>
               <button class="btn btn-outline-warning" data-act="reject" data-id="${c.id}" title="Отклонить (запомнить — не вернётся при следующем поиске)">
                   <i class="bi bi-x"></i>
               </button>
               <button class="btn btn-outline-secondary" data-act="defer" data-id="${c.id}" title="Отложить (вернёмся к нему позже)">
                   <i class="bi bi-pause"></i>
               </button>`
            : '';
        const deleteBtn = `<button class="btn btn-outline-danger" data-act="delete" data-id="${c.id}" title="Удалить навсегда (физически из БД; при повторном поиске может вернуться)">
                <i class="bi bi-trash"></i>
            </button>`;
        const statusLabel = c.status !== 'pending'
            ? `<small class="text-muted me-2">${escapeHtml(c.status)}</small>` : '';
        const actions = `${statusLabel}<div class="btn-group btn-group-sm" role="group">${baseButtons}${deleteBtn}</div>`;

        return `
            <div class="col-md-6" data-candidate-id="${c.id}">
                <div class="card h-100">
                    <div class="card-body p-2">
                        <div class="d-flex gap-2">
                            ${photo}
                            <div class="flex-grow-1 min-width-0">
                                <div class="d-flex align-items-start justify-content-between gap-2">
                                    <a href="${escapeAttr(vkLink)}" target="_blank" rel="noopener" class="fw-bold text-decoration-none">
                                        ${escapeHtml(c.name)} <i class="bi bi-box-arrow-up-right small"></i>
                                    </a>
                                    <div class="text-nowrap">${relevanceBadge}${infoBadge}${statusBadge}</div>
                                </div>
                                <div class="small text-muted">${escapeHtml(members)} · ${confidence}</div>
                                <div class="mt-1 small">${escapeHtml(c.ai_reasoning || 'AI не дал обоснования')}</div>
                                ${c.description ? `<div class="mt-1 small text-muted text-truncate-2">${escapeHtml(c.description)}</div>` : ''}
                            </div>
                        </div>
                        <div class="d-flex align-items-center gap-2 mt-2">
                            <select class="form-select form-select-sm category-select" data-id="${c.id}" style="max-width:240px;">
                                ${catOptions}
                            </select>
                            <div class="ms-auto">${actions}</div>
                        </div>
                    </div>
                </div>
            </div>`;
    }

    function bindCardEventListeners() {
        sectionsContainer.querySelectorAll('button[data-act]').forEach(btn => {
            btn.addEventListener('click', () => onAction(btn.dataset.act, parseInt(btn.dataset.id, 10)));
        });
        sectionsContainer.querySelectorAll('select.category-select').forEach(sel => {
            sel.addEventListener('change', () => onCategoryChange(parseInt(sel.dataset.id, 10), sel.value));
        });
    }

    async function onCategoryChange(id, newCategory) {
        const cand = candidates.find(c => c.id === id);
        if (!cand) return;
        const body = newCategory ? {category: newCategory} : {category: null};
        // backend требует ALLOWED_CATEGORIES, поэтому пустую категорию мы шлём
        // как "other" (escape hatch). На фронте просто перерисуем после ответа.
        if (!newCategory) body.category = 'other';
        setStatus('info', '⏳ Меняю категорию…');
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
            const data = await resp.json();
            cand.ai_category = data.candidate.ai_category;
            setStatus('success', '✓ Категория обновлена');
            renderSections();  // переотрисовка двигает карточку в новую секцию
            // commit-кнопку тоже могла затронуть
            updateCommitButtonState();
        } catch (e) {
            setStatus('danger', `Ошибка: ${e.message}`);
        }
    }

    function updateCommitButtonState() {
        const hasCommitable = candidates.some(
            c => c.status === 'pending' && c.ai_category && c.ai_category !== 'other'
        );
        commitBtn.disabled = !(hasCommitable && region && region.vk_group_id);
    }

    function onAction(act, id) {
        const cand = candidates.find(c => c.id === id);
        if (!cand) return;
        if (act === 'approve') {
            if (!cand.ai_category || cand.ai_category === 'other') {
                setStatus('warning', 'У этого кандидата нет конкретной категории — сначала выбери её в dropdown.');
                return;
            }
            return patchCandidate(id, {status: 'approved', category: cand.ai_category});
        }
        if (act === 'reject') return patchCandidate(id, {status: 'rejected'});
        if (act === 'defer') return patchCandidate(id, {status: 'deferred'});
        if (act === 'delete') {
            const label = cand.name || `#${cand.id}`;
            if (!confirm(`Удалить кандидата «${label}» из этого региона?\n\nЗапись будет физически стёрта из БД. При перезапуске поиска эта группа может появиться снова, если VK её снова отдаст. Для гарантии «больше никогда» используйте «Отклонить».`)) return;
            return deleteCandidate(id);
        }
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
            setStatus('success', `✓ ${body.status || body.category}`);
            await load();
        } catch (e) {
            setStatus('danger', `Ошибка: ${e.message}`);
        }
    }

    async function deleteCandidate(id) {
        setStatus('info', '⏳ Удаляю…');
        try {
            const resp = await fetch(`/api/discovery/candidates/${id}`, {method: 'DELETE'});
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${resp.status}`);
            }
            setStatus('success', '✓ Удалён');
            await load();
        } catch (e) {
            setStatus('danger', `Ошибка: ${e.message}`);
        }
    }

    async function rerunDiscovery() {
        if (!confirm('Перезапустить поиск сообществ?\n\nЗадача уйдёт в фон (Celery), UI не повиснет. Для крупных районов поиск может занять 5-10 минут.')) return;
        rerunBtn.disabled = true;
        const startedAt = Date.now();
        const elapsedSec = () => Math.floor((Date.now() - startedAt) / 1000);
        const setBtn = (text) => {
            rerunBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${text}`;
        };
        setBtn('Ставлю задачу…');
        try {
            const triggerResp = await fetch('/api/discovery/trigger-async', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({region_id: region.id}),
            });
            if (!triggerResp.ok) {
                const txt = await triggerResp.text();
                throw new Error(`HTTP ${triggerResp.status}: ${txt.slice(0, 200)}`);
            }
            const {task_id} = await triggerResp.json();
            if (!task_id) throw new Error('сервер не вернул task_id');

            const POLL_MS = 5000;
            const MAX_WAIT_SEC = 1800;
            while (true) {
                await new Promise(r => setTimeout(r, POLL_MS));
                const sec = elapsedSec();
                if (sec > MAX_WAIT_SEC) {
                    throw new Error(`таймаут ${MAX_WAIT_SEC}с (задача всё ещё работает в Celery — обнови страницу позже)`);
                }
                setBtn(`Ищу сообщества… ${sec}с`);
                const statusResp = await fetch(`/api/discovery/task/${task_id}`);
                if (!statusResp.ok) continue;
                const status = await statusResp.json();
                if (!status.ready) continue;
                if (status.state === 'SUCCESS') {
                    const r = status.result || {};
                    setStatus('success', `Найдено ${r.found || 0}, новых ${r.inserted || 0}, обновлено ${r.refreshed || 0}.`);
                    await load();
                    return;
                }
                if (status.state === 'FAILURE') {
                    throw new Error(status.error || 'задача завершилась с ошибкой');
                }
            }
        } catch (e) {
            setStatus('danger', `Ошибка: ${e.message}`);
        } finally {
            rerunBtn.disabled = false;
            rerunBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Перезапустить поиск';
        }
    }

    async function bulkReject() {
        const conf = parseInt(filterConfidence.value, 10) || 0;
        if (!confirm(`Отклонить всех ожидающих решения${conf ? ` с уверенностью AI ≥ ${conf}` : ''}?`)) return;
        setStatus('info', '⏳ Массовое отклонение…');
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

    async function commitRegion() {
        if (!confirm('Создать регион в Сетке?\n\n' +
                     'Все ожидающие решения кандидаты с конкретной категорией будут одобрены и подключены к расписанию. ' +
                     'Регион станет активным.')) return;
        commitBtn.disabled = true;
        commitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Создаю…';
        try {
            const resp = await fetch(`/api/discovery/commit/${region.id}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
            });
            const r = await resp.json();
            if (!resp.ok) throw new Error(r.detail || `HTTP ${resp.status}`);
            setStatus('success',
                `✅ Регион «${escapeHtml(r.region_code)}» активирован. ` +
                `Создано ${r.communities_created} сообществ, ${r.pending_left} осталось в ожидании.`);
            setTimeout(() => {
                window.location.href = `/regions`;
            }, 2500);
        } catch (e) {
            setStatus('danger', `Ошибка: ${e.message}`);
            commitBtn.disabled = false;
            commitBtn.innerHTML = '<i class="bi bi-check2-circle"></i> Создать регион в Сетке';
        }
    }

    function setStatus(level, html) {
        statusMsg.innerHTML = `<div class="alert alert-${level} py-2">${html}</div>`;
        if (level === 'success' || level === 'warning') {
            setTimeout(() => { statusMsg.innerHTML = ''; }, 4000);
        }
    }

    function sectionHeaderColor(key) {
        const map = {
            admin: 'bg-primary',
            novost: 'bg-info text-dark',
            reklama: 'bg-warning text-dark',
            sosed: 'bg-secondary',
            kultura: 'bg-success',
            sport: 'bg-success',
            detsad: 'bg-info text-dark',
            other: 'bg-light text-dark border',
            _none: 'bg-dark',
        };
        return map[key] || 'bg-secondary';
    }

    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    function escapeAttr(s) { return escapeHtml(s); }
})();
