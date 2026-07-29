// Страница /regions/links — список VK-сообществ сети одним куском.
// Данные и уже собранный текст приходят из GET /api/regions/vk-links: формат
// строки («Имя ИНФО — https://vk.com/clubN») собирается на сервере, чтобы у
// текста для копирования был один источник (modules/region_links.py).

let rlData = null;

async function loadRegionLinks() {
    const status = document.getElementById('rl-status');
    const container = document.getElementById('rl-blocks');
    status.textContent = 'Загрузка…';
    container.innerHTML = '';
    try {
        rlData = await apiClient.request('/regions/vk-links');
    } catch (e) {
        status.className = 'text-danger small mb-3';
        status.textContent = 'Не удалось загрузить список: ' + e.message;
        return;
    }
    status.className = 'text-muted small mb-3';
    status.textContent = `Сообществ в списке: ${rlData.total}`;
    renderRegionLinks(rlData);
}

function renderRegionLinks(data) {
    const container = document.getElementById('rl-blocks');
    if (!data.blocks.length) {
        container.innerHTML = '<div class="alert alert-warning">' +
            'Нет ни одного активного региона с заданной VK-группой.</div>';
        return;
    }
    container.innerHTML = data.blocks.map((block, idx) => `
        <div class="card mb-3">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <h6 class="mb-0">
                        <i class="bi bi-geo"></i> ${escapeHtml(block.title)}
                        <span class="text-muted small">(${block.items.length})</span>
                    </h6>
                    <button class="btn btn-outline-primary btn-sm rl-copy-block" data-block="${idx}">
                        <i class="bi bi-clipboard"></i> Копировать блок
                    </button>
                </div>
                <pre class="mb-0 p-2 bg-body-tertiary rounded small" style="white-space: pre-wrap;">${escapeHtml(block.text)}</pre>
            </div>
        </div>
    `).join('');

    container.querySelectorAll('.rl-copy-block').forEach(btn => {
        btn.addEventListener('click', () => {
            const block = data.blocks[parseInt(btn.dataset.block, 10)];
            copyToClipboard(block.text, btn);
        });
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// navigator.clipboard живёт только в secure context (https / localhost).
// На http-хосте (локальная отладка по IP) он undefined — тогда старый путь
// через скрытую textarea + execCommand, иначе кнопка молча не работает.
async function copyToClipboard(text, btn) {
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
    flashButton(btn, ok);
}

function flashButton(btn, ok) {
    if (!btn) return;
    const original = btn.innerHTML;
    btn.innerHTML = ok
        ? '<i class="bi bi-check2"></i> Скопировано'
        : '<i class="bi bi-x-lg"></i> Не вышло';
    setTimeout(() => { btn.innerHTML = original; }, 1500);
}

document.addEventListener('DOMContentLoaded', () => {
    loadRegionLinks();
    document.getElementById('rl-refresh').addEventListener('click', loadRegionLinks);
    document.getElementById('rl-copy-all').addEventListener('click', (e) => {
        if (!rlData) return;
        copyToClipboard(rlData.text, e.currentTarget);
    });
});
