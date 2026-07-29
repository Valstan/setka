// Публичный лендинг /regions/links — список сообществ сети + вкладка рекламы.
// Данные и готовый текст для копирования приходят из GET /api/regions/vk-links
// (публичный эндпоинт): формат строки «Имя ИНФО — 3657 — https://vk.com/...»
// собирается на сервере (modules/region_links.py), JS его не пересобирает.

let rlData = null;

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

// ── Список сообществ ───────────────────────────────────────────────────────

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
    renderStats(rlData);
    renderRegionLinks(rlData);
}

// Число с русскими разделителями тысяч: 15832 → «15 832».
function fmt(n) {
    return (n === null || n === undefined) ? '' : n.toLocaleString('ru-RU');
}

function renderStats(data) {
    document.getElementById('stat-groups').textContent = fmt(data.total);
    document.getElementById('stat-members').textContent =
        data.total_members ? fmt(data.total_members) : '—';
}

function renderRegionLinks(data) {
    const container = document.getElementById('rl-blocks');
    if (!data.blocks.length) {
        container.innerHTML = '<div class="error-box">Список пока пуст.</div>';
        return;
    }
    container.innerHTML = data.blocks.map((block, idx) => `
        <div class="oblast-card">
            <div class="oblast-head">
                <h3>${escapeHtml(block.title)}</h3>
                <div style="display:flex; gap:10px; align-items:center;">
                    <span class="count">${block.items.length} сообществ</span>
                    <button class="btn btn-ghost rl-copy-block" data-block="${idx}">⎘ Копировать блок</button>
                </div>
            </div>
            <ul class="community-list">
                ${block.items.map(item => `
                    <li${item.kind === 'oblast' ? ' class="oblast-row"' : ''}>
                        <span class="c-name">${escapeHtml(item.name)}</span>
                        ${item.members !== null ? `<span class="c-dash">—</span><span class="c-members">${fmt(item.members)}</span>` : ''}
                        <span class="c-dash">—</span>
                        <a class="c-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.url.replace('https://', ''))}</a>
                    </li>
                `).join('')}
            </ul>
        </div>
    `).join('');

    container.querySelectorAll('.rl-copy-block').forEach(btn => {
        btn.addEventListener('click', () => {
            const block = data.blocks[parseInt(btn.dataset.block, 10)];
            copyToClipboard(block.text, btn, '⎘ Копировать блок');
        });
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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

// ── Инициализация ──────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    loadRegionLinks();
    document.getElementById('rl-refresh').addEventListener('click', loadRegionLinks);
    document.getElementById('rl-copy-all').addEventListener('click', (e) => {
        if (!rlData) return;
        copyToClipboard(rlData.text, e.currentTarget, '⎘ Копировать весь список');
    });
    // Кнопка «Заказать» на тарифе кладёт выбранный пакет в буфер — клиент
    // вставляет его в сообщение при заказе (подсказано текстом рядом с контактами).
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
