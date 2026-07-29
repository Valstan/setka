// Публичный лендинг /regions/links — список сообществ сети + вкладка рекламы.
// Данные приходят из GET /api/regions/vk-links (публичный эндпоинт). Формат
// строки собирает сервер (modules/region_links.py) и кладёт в item.line —
// клиент только меняет ПОРЯДОК строк и склеивает их, но не пересобирает формат.

let rlData = null;                  // ответ API целиком
let rlItems = [];                   // плоский список сообществ
let rlSort = 'alpha';               // alpha | members | neighbors
let rlAnchor = null;                // код опорного района для сортировки по соседству
const rlPicked = new Set();         // коды отмеченных галочками сообществ

// ── Вкладки ────────────────────────────────────────────────────────────────

function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('show'));
            btn.classList.add('active');
            document.getElementById('pane-' + btn.dataset.tab).classList.add('show');
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    });
}

// ── Загрузка ───────────────────────────────────────────────────────────────

async function loadRegionLinks() {
    const status = document.getElementById('rl-status');
    const container = document.getElementById('rl-blocks');
    status.className = 'loading';
    status.textContent = 'Загружаем сеть…';
    container.innerHTML = '';
    try {
        const resp = await fetch('/api/regions/vk-links');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        rlData = await resp.json();
    } catch (e) {
        status.className = 'error-box';
        status.textContent = 'Не удалось загрузить список: ' + e.message;
        return;
    }
    status.textContent = '';
    status.className = '';
    rlItems = rlData.blocks.flatMap(b => b.items.map(i => ({ ...i, block: b.title })));
    renderStats(rlData);
    fillAnchorOptions();
    render();
}

function fmt(n) {
    return (n === null || n === undefined) ? '' : n.toLocaleString('ru-RU');
}

function renderStats(data) {
    document.getElementById('stat-groups').textContent = fmt(data.total);
    document.getElementById('stat-members').textContent =
        data.total_members ? fmt(data.total_members) : '—';
}

// Выпадашка «мой район» — только районы (области опорной точкой не бывают).
function fillAnchorOptions() {
    const sel = document.getElementById('rl-anchor');
    const raions = rlItems.filter(i => i.kind !== 'oblast');
    sel.innerHTML = raions
        .map(i => `<option value="${i.code}">${escapeHtml(i.name.replace(/ ИНФО$/, ''))}</option>`)
        .join('');
    if (!rlAnchor && raions.length) rlAnchor = raions[0].code;
    if (rlAnchor) sel.value = rlAnchor;
}

// ── Сортировка по соседству: кольца BFS по графу соседей ──────────────────
// Граф приходит с сервера по ВСЕМ регионам, включая ещё не запущенные: они
// транзитные узлы, без них цепочка соседства рвётся (Луза → Мураши → …).

function neighborRings(anchorCode) {
    const graph = (rlData && rlData.neighbors) || {};
    const depth = { [anchorCode]: 0 };
    let frontier = [anchorCode];
    while (frontier.length) {
        const next = [];
        for (const code of frontier) {
            for (const nb of (graph[code] || [])) {
                if (depth[nb] === undefined) {
                    depth[nb] = depth[code] + 1;
                    next.push(nb);
                }
            }
        }
        frontier = next;
    }
    return depth;
}

const RING_TITLES = [
    'Ваш район',
    'Соседние районы — граничат с вами',
    'Через один район',
    'Через два района',
];

// Областные группы соседей не имеют (они «над» районами) — им своя строка,
// иначе они молча падают в «остальные», хотя это самый широкий охват.
const OBLAST_RING = 'Областные сообщества — охват всей области разом';

function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
    return many;
}

function ringTitle(item, depth) {
    if (item.kind === 'oblast') return OBLAST_RING;
    const d = depth[item.code];
    if (d === undefined) return 'Остальные сообщества сети';
    return RING_TITLES[d] || `Дальше по сети — ${d} ${plural(d, 'район', 'района', 'районов')} от вас`;
}

// Порядок групп: кольца по возрастанию, затем несвязанные, областные — в конце.
function ringOrder(item, depth) {
    if (item.kind === 'oblast') return 10000;
    const d = depth[item.code];
    return d === undefined ? 9000 : d;
}

// ── Рендер ─────────────────────────────────────────────────────────────────

function render() {
    const container = document.getElementById('rl-blocks');
    if (!rlItems.length) {
        container.innerHTML = '<div class="error-box">Список пока пуст.</div>';
        return;
    }
    if (rlSort === 'neighbors') {
        renderByNeighbors(container);
    } else {
        renderByBlocks(container);
    }
    bindRowHandlers(container);
    updateSelbar();
}

// Алфавит и подписчики: сохраняем разбивку по областям.
function renderByBlocks(container) {
    const blocks = rlData.blocks.map(b => {
        const items = b.items.slice();
        if (rlSort === 'members') {
            items.sort((a, b2) => (b2.members || 0) - (a.members || 0));
        }
        return { ...b, items };
    });
    container.innerHTML = blocks.map((block, idx) => `
        <div class="oblast-card">
            <div class="oblast-head">
                <h3>${escapeHtml(block.title)}</h3>
                <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
                    <label class="head-check">
                        <input type="checkbox" class="block-check" data-block="${idx}">
                        выбрать все
                    </label>
                    <span class="count">${block.items.length} сообществ</span>
                    <button class="btn btn-ghost rl-copy-block" data-block="${idx}">⎘ Копировать блок</button>
                </div>
            </div>
            <ul class="community-list">
                ${block.items.map(item => rowHtml(item)).join('')}
            </ul>
        </div>
    `).join('');
    // Кнопки «копировать блок» берут ТЕКУЩИЙ порядок, а не серверный block.text.
    container.querySelectorAll('.rl-copy-block').forEach(btn => {
        btn.addEventListener('click', () => {
            const block = blocks[parseInt(btn.dataset.block, 10)];
            const text = [block.title + ':'].concat(block.items.map(i => i.line)).join('\n');
            copyToClipboard(text, btn, '⎘ Копировать блок');
        });
    });
    container.querySelectorAll('.block-check').forEach(chk => {
        chk.addEventListener('change', () => {
            const block = blocks[parseInt(chk.dataset.block, 10)];
            block.items.forEach(i => chk.checked ? rlPicked.add(i.code) : rlPicked.delete(i.code));
            render();
        });
    });
}

function sortedByNeighbors(depth) {
    return rlItems.slice().sort((a, b) => {
        const fa = ringOrder(a, depth), fb = ringOrder(b, depth);
        if (fa !== fb) return fa - fb;
        return sortKey(a.name).localeCompare(sortKey(b.name), 'ru');
    });
}

// Соседство: плоский список кольцами от выбранного района.
function renderByNeighbors(container) {
    const depth = neighborRings(rlAnchor);
    const groups = new Map();
    sortedByNeighbors(depth).forEach(item => {
        const key = ringTitle(item, depth);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
    });

    container.innerHTML = `
        <div class="oblast-card">
            <div class="oblast-head">
                <h3>От ближних к дальним</h3>
                <span class="count">${rlItems.length} сообществ · опора: ${escapeHtml(
                    (rlItems.find(i => i.code === rlAnchor) || {}).name || '—'
                )}</span>
            </div>
            ${[...groups.entries()].map(([title, items]) => `
                <div class="ring-head">${escapeHtml(title)} · ${items.length}</div>
                <ul class="community-list">
                    ${items.map(item => rowHtml(item, true)).join('')}
                </ul>
            `).join('')}
        </div>
    `;
}

function rowHtml(item, showOblast) {
    const picked = rlPicked.has(item.code);
    return `
        <li class="${item.kind === 'oblast' ? 'oblast-row' : ''} ${picked ? 'picked' : ''}">
            <input type="checkbox" class="c-check" data-code="${escapeHtml(item.code)}" ${picked ? 'checked' : ''}
                   title="Выбрать для заказа рекламы">
            <span class="c-name">${escapeHtml(item.name)}</span>
            ${item.members !== null ? `<span class="c-dash">—</span><span class="c-members">${fmt(item.members)}</span>` : ''}
            <span class="c-dash">—</span>
            <a class="c-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.url.replace('https://', ''))}</a>
            ${showOblast && item.oblast ? `<span class="c-oblast">${escapeHtml(item.oblast)}</span>` : ''}
        </li>
    `;
}

function bindRowHandlers(container) {
    container.querySelectorAll('.c-check').forEach(chk => {
        chk.addEventListener('change', () => {
            const code = chk.dataset.code;
            if (chk.checked) rlPicked.add(code); else rlPicked.delete(code);
            chk.closest('li').classList.toggle('picked', chk.checked);
            updateSelbar();
        });
    });
}

function sortKey(s) { return s.toLowerCase().replace(/ё/g, 'е'); }

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text === null || text === undefined ? '' : text;
    return div.innerHTML;
}

// ── Панель выбора ──────────────────────────────────────────────────────────

function pickedItems() {
    return rlItems.filter(i => rlPicked.has(i.code));
}

// Дешёвший вариант для N сообществ: поштучно или подходящий пакет.
// covers = на сколько сообществ рассчитан тариф (null = вся сеть).
function bestPrice(n) {
    let packages = [];
    try {
        packages = JSON.parse(document.getElementById('pkg-data').textContent);
    } catch (e) {
        return null;
    }
    const single = packages.find(p => p.covers === 1);
    if (!single) return null;
    const options = [{ price: single.price * n, title: `${n} × ${single.price} ₽ поштучно` }];
    packages.forEach(p => {
        if (p.covers === 1) return;
        const covers = p.covers === null ? Infinity : p.covers;
        if (covers >= n) options.push({ price: p.price, title: p.title.toLowerCase() });
    });
    options.sort((a, b) => a.price - b.price);
    return options[0];
}

// Тариф «вся сеть», если до него рукой подать: выбрано почти всё и доплата
// невелика — честнее показать это, чем молча посчитать поштучно дороже.
function wholeNetworkNudge(n, bestPriceValue) {
    let packages = [];
    try {
        packages = JSON.parse(document.getElementById('pkg-data').textContent);
    } catch (e) {
        return null;
    }
    const whole = packages.find(p => p.covers === null);
    if (!whole || !rlItems.length || n >= rlItems.length) return null;
    const diff = whole.price - bestPriceValue;
    if (diff <= 0 || diff > bestPriceValue * 0.25) return null;
    return { price: whole.price, diff, rest: rlItems.length - n };
}

function updateSelbar() {
    const bar = document.getElementById('rl-selbar');
    const items = pickedItems();
    if (!items.length) {
        bar.hidden = true;
        return;
    }
    bar.hidden = false;
    const reach = items.reduce((s, i) => s + (i.members || 0), 0);
    document.getElementById('sel-n').textContent = fmt(items.length);
    document.getElementById('sel-reach').textContent = fmt(reach);
    const best = bestPrice(items.length);
    if (!best) {
        document.getElementById('sel-price').innerHTML = '';
        return;
    }
    const nudge = wholeNetworkNudge(items.length, best.price);
    document.getElementById('sel-price').innerHTML =
        `≈ ${fmt(best.price)} ₽ <span class="hint">(${escapeHtml(best.title)}; точную цену подтвердим при заказе)</span>` +
        (nudge
            ? `<span class="nudge">🔥 +${fmt(nudge.diff)} ₽ — и вся сеть, ещё ${nudge.rest} ${plural(nudge.rest, 'сообщество', 'сообщества', 'сообществ')}</span>`
            : '');
}

function orderText() {
    const items = pickedItems();
    const reach = items.reduce((s, i) => s + (i.members || 0), 0);
    const best = bestPrice(items.length);
    const lines = [
        `Здравствуйте! Хочу разместить рекламу в сети САРАФАН — выбрал ${items.length} сообществ:`,
        '',
    ];
    lines.push(...items.map(i => i.line));
    lines.push('');
    lines.push(`Суммарный охват: ${fmt(reach)} подписчиков.`);
    if (best) lines.push(`Ориентировочно по вашим расценкам: ${fmt(best.price)} ₽ (${best.title}).`);
    return lines.join('\n');
}

// ── Копирование ────────────────────────────────────────────────────────────
// navigator.clipboard живёт только в secure context (https / localhost);
// иначе — fallback через скрытую textarea + execCommand.

async function copyToClipboard(text, btn, restoreLabel) {
    let ok = false;
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            ok = true;
        }
    } catch (e) {
        ok = false;
    }
    if (!ok) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try {
            ok = document.execCommand('copy');
        } catch (e) {
            ok = false;
        }
        document.body.removeChild(ta);
    }
    flashButton(btn, ok, restoreLabel);
}

function flashButton(btn, ok, restoreLabel) {
    if (!btn) return;
    const original = restoreLabel || btn.innerHTML;
    btn.innerHTML = ok ? '✓ Скопировано' : '✗ Не вышло';
    setTimeout(() => { btn.innerHTML = original; }, 1600);
}

// Полный текст в текущем порядке — «копировать весь список» уважает сортировку.
function currentFullText() {
    if (rlSort !== 'neighbors') {
        return rlData.blocks
            .map(b => {
                const items = b.items.slice();
                if (rlSort === 'members') items.sort((a, b2) => (b2.members || 0) - (a.members || 0));
                return [b.title + ':'].concat(items.map(i => i.line)).join('\n');
            })
            .join('\n\n');
    }
    const depth = neighborRings(rlAnchor);
    const out = [];
    let current = null;
    sortedByNeighbors(depth).forEach(item => {
        const title = ringTitle(item, depth);
        if (title !== current) {
            if (current !== null) out.push('');
            out.push(title + ':');
            current = title;
        }
        out.push(item.line);
    });
    return out.join('\n');
}

// ── Инициализация ──────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    loadRegionLinks();

    document.getElementById('rl-refresh').addEventListener('click', loadRegionLinks);
    document.getElementById('rl-copy-all').addEventListener('click', (e) => {
        if (!rlData) return;
        copyToClipboard(currentFullText(), e.currentTarget, '⎘ Копировать весь список');
    });

    document.querySelectorAll('#rl-sort .seg-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#rl-sort .seg-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            rlSort = btn.dataset.sort;
            document.getElementById('rl-anchor-wrap').hidden = rlSort !== 'neighbors';
            if (rlData) render();
        });
    });
    document.getElementById('rl-anchor').addEventListener('change', (e) => {
        rlAnchor = e.target.value;
        render();
    });

    document.getElementById('sel-clear').addEventListener('click', () => {
        rlPicked.clear();
        render();
    });
    document.getElementById('sel-copy').addEventListener('click', (e) => {
        copyToClipboard(orderText(), e.currentTarget, '⎘ Скопировать заказ');
    });

    // Кнопка «Заказать» на тарифе кладёт выбранный пакет в буфер.
    document.querySelectorAll('.pkg-order').forEach(btn => {
        btn.addEventListener('click', () => {
            copyToClipboard(
                'Хочу заказать рекламу в САРАФАНЕ: ' + btn.dataset.pkg,
                btn,
                'Заказать'
            );
        });
    });
});
