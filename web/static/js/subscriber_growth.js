/* Сравнительная динамика роста подписчиков ГЛАВНЫХ ИНФО-групп регионов.
 *
 * Один мульти-line Chart.js; чекбоксы под графиком переключают серии-регионы,
 * кнопки-агрегаты — линии «Σ область» (сумма с дублями) и «область без дублей»
 * (недельный дедуп). Список регионов сгруппирован по областям (Кировская /
 * Татарстан отдельно) и отсортирован по числу подписчиков.
 *
 * Данные: apiClient.getGrowthRegions(days) — список+области для панели;
 * apiClient.getGrowthSeries(ids, days, oblastSum, oblastUniq) — ряды.
 */
(function () {
    'use strict';

    const state = {
        regions: [],          // [{id,name,delta,delta_pct,latest_count,oblast_id,oblast_name,...}]
        oblasts: [],          // [{id,name,region_count,latest_sum,latest_unique}]
        selected: new Set(),  // выбранные region_id
        sumSel: new Set(),    // области с включённой линией Σ (с дублями)
        uniqSel: new Set(),   // области с включённой линией «без дублей»
        days: 90,
    };
    let chart = null;
    let seriesTimer = null;

    // Стабильная палитра: цвет привязан к индексу серии в текущей выборке.
    const PALETTE = [
        '#0d6efd', '#dc3545', '#198754', '#fd7e14', '#6f42c1', '#20c997',
        '#d63384', '#0dcaf0', '#ffc107', '#6610f2', '#198038', '#b58900',
    ];
    const colorFor = (i) => PALETTE[i % PALETTE.length];

    function el(id) { return document.getElementById(id); }
    function esc(s) { return (s || '').replace(/"/g, '&quot;'); }

    function fmtDelta(c) {
        if (c.points < 2) return '<span class="text-muted">нет динамики</span>';
        const sign = c.delta > 0 ? '+' : '';
        const cls = c.delta > 0 ? 'text-success' : (c.delta < 0 ? 'text-danger' : 'text-muted');
        return `<span class="${cls}">${sign}${c.delta} (${sign}${c.delta_pct}%)</span>`;
    }

    function rowHtml(c) {
        const checked = state.selected.has(c.id) ? 'checked' : '';
        const lag = c.is_laggard
            ? ' <span class="badge bg-warning text-dark">отстаёт</span>' : '';
        return `
        <div class="col-md-6">
          <label class="d-flex align-items-center gap-2 p-1 rounded growth-row">
            <input type="checkbox" class="form-check-input mt-0 growth-cb" value="${c.id}" ${checked}>
            <span class="text-truncate" style="max-width: 220px;" title="${esc(c.name)}">${c.name || ('#' + c.id)}</span>
            ${lag}
            <span class="ms-auto small">${c.latest_count ?? '—'} · ${fmtDelta(c)}</span>
          </label>
        </div>`;
    }

    function renderList() {
        const list = el('growth-list');
        if (!state.regions.length) {
            list.innerHTML = '<div class="text-muted small">Снимков пока нет — копятся раз в сутки.</div>';
            return;
        }
        const q = (el('growth-search').value || '').trim().toLowerCase();
        const items = state.regions.filter(c => !q || (c.name || '').toLowerCase().includes(q));
        if (!items.length) {
            list.innerHTML = '<div class="text-muted small">Ничего не найдено.</div>';
            return;
        }
        // Группировка по области; порядок групп — как в state.oblasts (по Σ убыв.),
        // «Без области» в конце. Внутри группы — по числу подписчиков убыв.
        const groups = new Map();
        items.forEach(c => {
            const key = c.oblast_id == null ? '__none__' : String(c.oblast_id);
            if (!groups.has(key)) groups.set(key, { name: c.oblast_name, items: [] });
            groups.get(key).items.push(c);
        });
        const order = state.oblasts.map(o => String(o.id));
        const keys = Array.from(groups.keys()).sort((a, b) => {
            if (a === '__none__') return 1;
            if (b === '__none__') return -1;
            return order.indexOf(a) - order.indexOf(b);
        });
        const html = [];
        keys.forEach(k => {
            const g = groups.get(k);
            g.items.sort((x, y) => (y.latest_count || 0) - (x.latest_count || 0));
            const title = k === '__none__' ? 'Без области' : (g.name || ('Область #' + k));
            html.push(`<div class="col-12 mt-2 mb-1"><h6 class="mb-0 text-uppercase small text-muted border-bottom pb-1">${esc(title)} <span class="text-secondary">(${g.items.length})</span></h6></div>`);
            g.items.forEach(c => html.push(rowHtml(c)));
        });
        list.innerHTML = html.join('');
    }

    function renderAggButtons() {
        const box = el('growth-oblast-aggs');
        if (!box) return;
        if (!state.oblasts.length) { box.innerHTML = ''; return; }
        const html = [];
        state.oblasts.forEach(o => {
            const sumOn = state.sumSel.has(o.id) ? 'active' : '';
            html.push(`<button type="button" class="btn btn-sm btn-outline-dark growth-agg-sum ${sumOn}" data-ob="${o.id}" title="Сумма подписчиков всех групп области (с дублями)">Σ ${esc(o.name)} <span class="badge bg-secondary">${o.latest_sum ?? '—'}</span></button>`);
            const hasUniq = o.latest_unique != null;
            const uniqOn = state.uniqSel.has(o.id) ? 'active' : '';
            const dis = hasUniq ? '' : 'disabled';
            const uniqTitle = hasUniq
                ? 'Уникальные подписчики области (без дублей, пересчёт раз в неделю)'
                : 'Дедуп ещё не считался — появится после ночного пересчёта';
            const uniqBadge = hasUniq ? `<span class="badge bg-success">${o.latest_unique}</span>` : '';
            html.push(`<button type="button" class="btn btn-sm btn-outline-success growth-agg-uniq ${uniqOn}" data-ob="${o.id}" ${dis} title="${esc(uniqTitle)}">${esc(o.name)} без дублей ${uniqBadge}</button>`);
        });
        box.innerHTML = html.join('');
    }

    function renderSummary() {
        const lag = state.regions.filter(c => c.is_laggard).length;
        const aggs = state.sumSel.size + state.uniqSel.size;
        el('growth-summary').textContent =
            `— ${state.regions.length} со снимками, выбрано ${state.selected.size}`
            + (aggs ? ` + ${aggs} агрег.` : '')
            + (lag ? `, отстающих ${lag}` : '');
    }

    function scheduleSeries() {
        clearTimeout(seriesTimer);
        seriesTimer = setTimeout(loadSeries, 250);
    }

    async function loadRegions() {
        const list = el('growth-list');
        list.innerHTML = '<div class="text-muted small">Загрузка…</div>';
        try {
            const data = await apiClient.getGrowthRegions(state.days);
            state.regions = (data && data.regions) || [];
            state.oblasts = (data && data.oblasts) || [];
        } catch (e) {
            console.error('growth regions load failed', e);
            list.innerHTML = '<div class="text-danger small">Ошибка загрузки списка.</div>';
            return;
        }
        // Очистить выбор от исчезнувших id.
        const present = new Set(state.regions.map(c => c.id));
        state.selected.forEach(id => { if (!present.has(id)) state.selected.delete(id); });
        const obIds = new Set(state.oblasts.map(o => o.id));
        state.sumSel.forEach(id => { if (!obIds.has(id)) state.sumSel.delete(id); });
        state.uniqSel.forEach(id => { if (!obIds.has(id)) state.uniqSel.delete(id); });
        // Первая загрузка без выбора — авто-выбрать топ-5 растущих, чтобы график не пустовал.
        if (!state.selected.size && !state.sumSel.size && !state.uniqSel.size && state.regions.length) {
            state.regions.slice(0, 5).forEach(c => state.selected.add(c.id));
        }
        renderList();
        renderAggButtons();
        renderSummary();
        loadSeries();
    }

    async function loadSeries() {
        const empty = el('growth-empty');
        const ids = Array.from(state.selected);
        const sums = Array.from(state.sumSel);
        const uniqs = Array.from(state.uniqSel);
        if (!ids.length && !sums.length && !uniqs.length) {
            if (chart) { chart.destroy(); chart = null; }
            empty.classList.remove('d-none');
            empty.textContent = 'Выберите регионы галочками или области кнопками, чтобы построить график.';
            return;
        }
        let data;
        try {
            data = await apiClient.getGrowthSeries(ids, state.days, sums, uniqs);
        } catch (e) {
            console.error('growth series load failed', e);
            return;
        }
        renderChart(data);
        renderSummary();
    }

    function datasetFor(s, i) {
        const color = colorFor(i);
        const base = {
            label: s.name,
            data: s.data,
            borderColor: color,
            backgroundColor: color,
            spanGaps: true,
            tension: 0.25,
            pointRadius: 2,
            borderWidth: 2,
        };
        if (s.kind === 'oblast_sum') {
            return Object.assign(base, { borderWidth: 3.5, pointRadius: 3 });
        }
        if (s.kind === 'oblast_uniq') {
            return Object.assign(base, { borderWidth: 3, borderDash: [6, 4], pointRadius: 3 });
        }
        return base;
    }

    function renderChart(data) {
        const empty = el('growth-empty');
        const labels = (data && data.labels) || [];
        const series = (data && data.series) || [];
        const hasPoints = labels.length > 0 && series.some(s => (s.data || []).some(v => v != null));
        if (!hasPoints) {
            if (chart) { chart.destroy(); chart = null; }
            empty.classList.remove('d-none');
            empty.textContent = labels.length <= 1
                ? 'Пока только один снимок — кривая появится со второго дня накопления.'
                : 'Нет точек для выбранной выборки за период.';
            return;
        }
        empty.classList.add('d-none');
        const datasets = series.map(datasetFor);
        const ctx = el('chart-growth');
        if (chart) chart.destroy();
        chart = new Chart(ctx, {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true,
                interaction: { mode: 'index', intersect: false },
                plugins: { legend: { display: true, labels: { boxWidth: 12 } } },
                scales: { y: { ticks: { precision: 0 } } },
            },
        });
    }

    function bind() {
        el('growth-days').addEventListener('change', (e) => {
            state.days = parseInt(e.target.value, 10) || 90;
            loadRegions();
        });
        el('growth-refresh').addEventListener('click', loadRegions);
        el('growth-search').addEventListener('input', renderList);
        el('growth-clear').addEventListener('click', () => {
            state.selected.clear();
            state.sumSel.clear();
            state.uniqSel.clear();
            renderList();
            renderAggButtons();
            renderSummary();
            loadSeries();
        });
        el('growth-pick-top').addEventListener('click', () => {
            state.selected.clear();
            state.regions.slice(0, 5).forEach(c => state.selected.add(c.id));
            renderList();
            renderSummary();
            loadSeries();
        });
        el('growth-pick-laggards').addEventListener('click', () => {
            state.selected.clear();
            state.regions.filter(c => c.is_laggard).slice(0, 15)
                .forEach(c => state.selected.add(c.id));
            renderList();
            renderSummary();
            loadSeries();
        });
        // Делегирование для чекбоксов (список перерисовывается).
        el('growth-list').addEventListener('change', (e) => {
            const cb = e.target.closest('.growth-cb');
            if (!cb) return;
            const id = parseInt(cb.value, 10);
            if (cb.checked) state.selected.add(id); else state.selected.delete(id);
            renderSummary();
            scheduleSeries();
        });
        // Кнопки-агрегаты по областям.
        el('growth-oblast-aggs').addEventListener('click', (e) => {
            const btn = e.target.closest('.growth-agg-sum, .growth-agg-uniq');
            if (!btn || btn.disabled) return;
            const ob = parseInt(btn.dataset.ob, 10);
            const set = btn.classList.contains('growth-agg-sum') ? state.sumSel : state.uniqSel;
            if (set.has(ob)) set.delete(ob); else set.add(ob);
            btn.classList.toggle('active');
            renderSummary();
            scheduleSeries();
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        bind();
        loadRegions();
    });
})();
