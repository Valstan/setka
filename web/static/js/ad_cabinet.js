// Рекламный кабинет — инбокс заявок из предложки.

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const STATUS_BADGE = {
    new: '<span class="badge bg-warning text-dark">Новая</span>',
    contacted: '<span class="badge bg-info text-dark">Связались</span>',
    published: '<span class="badge bg-success">Опубликовано</span>',
    skipped: '<span class="badge bg-secondary">Пропущено</span>',
};

document.addEventListener('DOMContentLoaded', async () => {
    await loadAdTemplates();
    await loadOfferImages();
    await loadAdRequests();
    const sel = document.getElementById('filter-status');
    if (sel) sel.addEventListener('change', loadAdRequests);
    // Делегированное удаление картинок (имена могут содержать пробелы/кириллицу).
    const box = document.getElementById('offer-images');
    if (box) box.addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-del-offer');
        if (btn) deleteOfferImage(btn.dataset.name);
    });
});

// --------------------------------------------------- библиотека картинок

async function loadOfferImages() {
    const box = document.getElementById('offer-images');
    if (!box) return;
    try {
        const data = await apiClient.getOfferImages();
        const images = data.images || [];
        if (!images.length) {
            box.innerHTML = '<div class="text-muted small">Картинок пока нет. ' +
                'Загрузите прайс / условия / портфолио (JPG или PNG).</div>';
            return;
        }
        box.innerHTML = images.map((img, i) => `
            <div class="text-center me-2 mb-2" style="width:108px;">
                <label class="d-block" style="cursor:pointer;" title="${escapeHtml(img.name)}">
                    <img src="${escapeHtml(img.url)}" loading="lazy"
                         style="width:100px;height:100px;object-fit:cover;border-radius:8px;border:1px solid #dee2e6;">
                    <div class="form-check d-flex justify-content-center mt-1 mb-0">
                        <input class="form-check-input offer-img-check" type="checkbox"
                               value="${escapeHtml(img.name)}" id="oi-${i}">
                    </div>
                </label>
                <button class="btn btn-sm btn-outline-danger py-0 px-1 btn-del-offer"
                        data-name="${escapeHtml(img.name)}" title="Удалить">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        `).join('');
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">Ошибка загрузки картинок: ${escapeHtml(e.message)}</div>`;
    }
}

function selectedOfferImages() {
    return Array.from(document.querySelectorAll('.offer-img-check:checked')).map(c => c.value);
}

async function uploadOfferImages(input) {
    const files = Array.from(input.files || []);
    const status = document.getElementById('offer-upload-status');
    for (const f of files) {
        try {
            if (status) status.textContent = `Загрузка «${f.name}»…`;
            await apiClient.uploadOfferImage(f);
        } catch (e) {
            if (status) status.textContent = `Ошибка «${f.name}»: ${e.message}`;
            input.value = '';
            await loadOfferImages();
            return;
        }
    }
    input.value = '';
    if (status) status.textContent = files.length ? 'Загружено ✓' : '';
    await loadOfferImages();
}

async function deleteOfferImage(name) {
    if (!name || !confirm(`Удалить картинку «${name}»?`)) return;
    try {
        await apiClient.deleteOfferImage(name);
        await loadOfferImages();
    } catch (e) {
        const status = document.getElementById('offer-upload-status');
        if (status) status.textContent = `Не удалось удалить: ${e.message}`;
    }
}

async function loadAdTemplates() {
    const select = document.getElementById('ad-template');
    try {
        const data = await apiClient.getAdTemplates();
        const templates = (data.templates || []).filter(t => !t.category || t.category === 'ad_offer' || true);
        if (templates.length) {
            select.innerHTML = templates.map(t =>
                `<option value="${t.id}">${escapeHtml(t.title)}${t.category ? ' [' + escapeHtml(t.category) + ']' : ''}</option>`
            ).join('');
        }
    } catch (e) {
        console.error('Не удалось загрузить шаблоны:', e);
    }
}

async function loadAdRequests() {
    const loading = document.getElementById('ad-loading');
    const empty = document.getElementById('ad-empty');
    const list = document.getElementById('ad-list');
    loading.style.display = '';
    empty.style.display = 'none';
    list.innerHTML = '';

    try {
        const status = document.getElementById('filter-status').value;
        const data = await apiClient.getAdRequests({ status });
        const requests = data.requests || [];
        loading.style.display = 'none';
        if (!requests.length) {
            empty.style.display = '';
            return;
        }
        list.innerHTML = requests.map(renderCard).join('');
    } catch (e) {
        loading.style.display = 'none';
        list.innerHTML = `<div class="alert alert-danger">Ошибка загрузки: ${escapeHtml(e.message)}</div>`;
    }
}

function renderCard(ar) {
    const reasons = (ar.reasons_json || []).map(r => `<span class="badge bg-light text-dark border me-1">${escapeHtml(r)}</span>`).join(' ');
    const photos = (ar.photo_urls_json || []).slice(0, 6).map(u =>
        `<a href="${escapeHtml(u)}" target="_blank"><img src="${escapeHtml(u)}" style="height:64px;border-radius:6px;margin:2px;" loading="lazy"></a>`
    ).join('');
    const authorLabel = ar.author_is_group
        ? `<i class="bi bi-people"></i> ${escapeHtml(ar.author_name || 'сообщество')} (автор — группа)`
        : `<i class="bi bi-person"></i> ${escapeHtml(ar.author_name || 'без имени')}`;
    const authorLink = ar.author_url
        ? `<a href="${escapeHtml(ar.author_url)}" target="_blank">${authorLabel}</a>` : authorLabel;

    return `
    <div class="card mb-3" id="ad-card-${ar.id}">
        <div class="card-body">
            <div class="d-flex justify-content-between align-items-start mb-2">
                <div>
                    <div class="fw-bold">${authorLink}</div>
                    <div class="text-muted small">
                        в «${escapeHtml(ar.community_name || '')}» ·
                        <a href="${escapeHtml(ar.vk_post_url || '#')}" target="_blank">пост в VK</a>
                    </div>
                </div>
                <div class="text-end">
                    ${STATUS_BADGE[ar.status] || escapeHtml(ar.status)}
                    <div class="text-muted small mt-1">score: ${ar.score}</div>
                </div>
            </div>
            <div class="mb-2">${reasons}</div>
            <div class="border rounded p-2 mb-2 bg-light small" style="white-space:pre-wrap;max-height:160px;overflow:auto;">${escapeHtml(ar.text_snapshot || '')}</div>
            ${photos ? `<div class="mb-2">${photos}</div>` : ''}

            <label class="form-label small mb-1">Ответ автору</label>
            <textarea class="form-control mb-2" id="prep-${ar.id}" rows="4"
                      placeholder="Нажмите «Подготовить из шаблона» или впишите вручную…">${escapeHtml(ar.prepared_message || '')}</textarea>

            <div class="d-flex flex-wrap gap-2">
                <button class="btn btn-sm btn-outline-primary" onclick="prepareCard(${ar.id})">
                    <i class="bi bi-magic"></i> Подготовить из шаблона
                </button>
                <button class="btn btn-sm btn-primary" onclick="sendCard(${ar.id})">
                    <i class="bi bi-send"></i> Отправить от сообщества
                </button>
                <button class="btn btn-sm btn-outline-secondary" onclick="copyCard(${ar.id})">
                    <i class="bi bi-clipboard"></i> Копировать
                </button>
                <button class="btn btn-sm btn-outline-success" onclick="markCard(${ar.id}, 'published')">
                    <i class="bi bi-check2"></i> Опубликовано
                </button>
                <button class="btn btn-sm btn-outline-dark" onclick="markCard(${ar.id}, 'skipped')">
                    <i class="bi bi-x"></i> Пропустить
                </button>
            </div>
            <div class="small mt-2" id="res-${ar.id}"></div>
        </div>
    </div>`;
}

function _res(id, html, cls) {
    const el = document.getElementById(`res-${id}`);
    if (el) el.innerHTML = `<span class="text-${cls || 'muted'}">${html}</span>`;
}

async function prepareCard(id) {
    const tplId = document.getElementById('ad-template').value;
    if (!tplId) { _res(id, 'Сначала выберите шаблон вверху страницы.', 'danger'); return; }
    try {
        const res = await apiClient.prepareAdReply(id, parseInt(tplId));
        const ta = document.getElementById(`prep-${id}`);
        if (ta) ta.value = res.prepared_message || '';
        _res(id, 'Текст подготовлен.', 'success');
    } catch (e) {
        _res(id, 'Ошибка подготовки: ' + escapeHtml(e.message), 'danger');
    }
}

async function copyCard(id) {
    const ta = document.getElementById(`prep-${id}`);
    if (!ta) return;
    try {
        await navigator.clipboard.writeText(ta.value);
        _res(id, 'Скопировано в буфер обмена.', 'success');
    } catch (e) {
        ta.select();
        _res(id, 'Выделено — нажмите Ctrl+C.', 'muted');
    }
}

async function sendCard(id) {
    const ta = document.getElementById(`prep-${id}`);
    if (!ta || !ta.value.trim()) { _res(id, 'Сначала подготовьте текст ответа.', 'danger'); return; }
    // Шлём именно то, что в поле (правки оператора), + выбранные картинки.
    const images = selectedOfferImages();
    _res(id, 'Отправляем…', 'muted');
    try {
        const res = await apiClient.sendAdReply(id, { message: ta.value, images });
        if (res.success) {
            _res(id, res.already_sent ? 'Уже было отправлено ранее.' : 'Отправлено от сообщества ✓', 'success');
            setTimeout(loadAdRequests, 800);
            return;
        }
        if (res.allowed === false) {
            if (res.personal_deeplink) {
                _res(id,
                    `VK не разрешает сообществу писать этому пользователю. ` +
                    `<a href="${escapeHtml(res.personal_deeplink)}" target="_blank">Открыть личный диалог</a> ` +
                    `и вставить текст (кнопка «Копировать»).`, 'warning');
            } else {
                _res(id, 'Автор — сообщество: личное сообщение невозможно. Ответьте вручную в VK.', 'warning');
            }
            return;
        }
        _res(id, 'Не отправлено: ' + escapeHtml(res.error || ('код ' + res.error_code)), 'danger');
    } catch (e) {
        _res(id, 'Ошибка отправки: ' + escapeHtml(e.message), 'danger');
    }
}

async function markCard(id, status) {
    try {
        await apiClient.setAdStatus(id, status);
        setTimeout(loadAdRequests, 300);
    } catch (e) {
        _res(id, 'Ошибка смены статуса: ' + escapeHtml(e.message), 'danger');
    }
}
