/* Сравнительная динамика роста подписчиков сообществ.
 *
 * Один мульти-line Chart.js; чекбоксы под графиком переключают серии. Метрика —
 * подписчики (members_count из дневных снимков `community_member_snapshots`).
 * Данные: apiClient.getGrowthCommunities(days) — список для чекбоксов со сводкой
 * роста; apiClient.getGrowthSeries(ids, days) — ряды для выбранных сообществ.
 */
(function () {
    'use strict';

    const state = {
        communities: [],      // [{id,name,delta,delta_pct,latest_count,points,is_laggard,...}]
        selected: new Set(),  // выбранные community_id
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

    function fmtDelta(c) {
        if (c.points < 2) return '<span class="text-muted">нет динамики</span>';
        const sign = c.delta > 0 ? '+' : '';
        const cls = c.delta > 0 ? 'text-success' : (c.delta < 0 ? 'text-danger' : 'text-muted');
        return `<span class="${cls}">${sign}${c.delta} (${sign}${c.delta_pct}%)</span>`;
    }

    function renderList() {
        const list = el('growth-list');
        const q = (el('growth-search').value || '').trim().toLowerCase();
        const items = state.communities.filter(c => !q || (c.name || '').toLowerCase().includes(q));
        if (!state.communities.length) {
            list.innerHTML = '<div class="text-muted small">Снимков пока нет — копятся раз в сутки.</div>';
            return;
        }
        if (!items.length) {
            list.innerHTML = '<div class="text-muted small">Ничего не найдено.</div>';
            return;
        }
        list.innerHTML = items.map(c => {
            const checked = state.selected.has(c.id) ? 'checked' : '';
            const lag = c.is_laggard
                ? ' <span class="badge bg-warning text-dark">отстаёт</span>' : '';
            const cat = c.category ? `<span class="text-muted small">${c.category}</span>` : '';
            return `
            <div class="col-md-6">
              <label class="d-flex align-items-center gap-2 p-1 rounded growth-row">
                <input type="checkbox" class="form-check-input mt-0 growth-cb" value="${c.id}" ${checked}>
                <span class="text-truncate" style="max-width: 230px;" title="${(c.name || '').replace(/"/g, '&quot;')}">${c.name || ('#' + c.id)}</span>
                ${lag}
                <span class="ms-auto small">${c.latest_count ?? '—'} · ${fmtDelta(c)}</span>
                ${cat}
              </label>
            </div>`;
        }).join('');
    }

    function renderSummary() {
        const lag = state.communities.filter(c => c.is_laggard).length;
        el('growth-summary').textContent =
            `— ${state.communities.length} со снимками, выбрано ${state.selected.size}`
            + (lag ? `, отстающих ${lag}` : '');
    }

    function scheduleSeries() {
        clearTimeout(seriesTimer);
        seriesTimer = setTimeout(loadSeries, 250);
    }

    async function loadCommunities() {
        const list = el('growth-list');
        list.innerHTML = '<div class="text-muted small">Загрузка…</div>';
        try {
            const data = await apiClient.getGrowthCommunities(state.days);
            state.communities = (data && data.communities) || [];
        } catch (e) {
            console.error('growth communities load failed', e);
            list.innerHTML = '<div class="text-danger small">Ошибка загрузки списка.</div>';
            return;
        }
        // Очистить выбор от исчезнувших id.
        const present = new Set(state.communities.map(c => c.id));
        state.selected.forEach(id => { if (!present.has(id)) state.selected.delete(id); });
        // Первая загрузка без выбора — авто-выбрать топ-5 растущих, чтобы график не пустовал.
        if (!state.selected.size && state.communities.length) {
            state.communities.slice(0, 5).forEach(c => state.selected.add(c.id));
        }
        renderList();
        renderSummary();
        loadSeries();
    }

    async function loadSeries() {
        const empty = el('growth-empty');
        const ids = Array.from(state.selected);
        if (!ids.length) {
            if (chart) { chart.destroy(); chart = null; }
            empty.classList.remove('d-none');
            empty.textContent = 'Выберите сообщества галочками, чтобы построить график.';
            return;
        }
        let data;
        try {
            data = await apiClient.getGrowthSeries(ids, state.days);
        } catch (e) {
            console.error('growth series load failed', e);
            return;
        }
        renderChart(data);
        renderSummary();
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
                : 'Нет точек для выбранных сообществ за период.';
            return;
        }
        empty.classList.add('d-none');
        const datasets = series.map((s, i) => ({
            label: s.name,
            data: s.data,
            borderColor: colorFor(i),
            backgroundColor: colorFor(i),
            spanGaps: true,
            tension: 0.25,
            pointRadius: 2,
            borderWidth: 2,
        }));
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
            loadCommunities();
        });
        el('growth-refresh').addEventListener('click', loadCommunities);
        el('growth-search').addEventListener('input', renderList);
        el('growth-clear').addEventListener('click', () => {
            state.selected.clear();
            renderList();
            renderSummary();
            loadSeries();
        });
        el('growth-pick-top').addEventListener('click', () => {
            state.selected.clear();
            state.communities.slice(0, 5).forEach(c => state.selected.add(c.id));
            renderList();
            renderSummary();
            loadSeries();
        });
        el('growth-pick-laggards').addEventListener('click', () => {
            state.selected.clear();
            state.communities.filter(c => c.is_laggard).slice(0, 15)
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
    }

    document.addEventListener('DOMContentLoaded', () => {
        bind();
        loadCommunities();
    });
})();
