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
    deleted: '<span class="badge bg-danger">Удалено</span>',
    vanished: '<span class="badge bg-dark" title="Поста больше нет в предложке VK — автор удалил или VK снял">Исчез из предложки</span>',
};

const ORIGIN_BADGE = {
    suggested: '<span class="badge bg-light text-dark border"><i class="bi bi-inbox"></i> предложка</span>',
    inbound_dm: '<span class="badge bg-primary"><i class="bi bi-chat-dots"></i> личка</span>',
};

// Бейдж приоритета по score (триаж): инбокс уже отсортирован score desc, бейдж
// делает сигнал видимым. Порог 3 = SCORE_THRESHOLD классификатора (явные
// коммерческие признаки → «стоит обработать»); 1–2 — слабый сигнал; 0 — шум.
function scoreBadge(score) {
    const s = Number(score) || 0;
    if (s >= 3) return `<span class="badge bg-success" title="Явные коммерческие признаки — приоритет"><i class="bi bi-fire"></i> score ${s}</span>`;
    if (s >= 1) return `<span class="badge bg-warning text-dark" title="Слабый сигнал">score ${s}</span>`;
    return `<span class="badge bg-light text-dark border" title="Без коммерческих признаков (шум)">score ${s}</span>`;
}

// Кэш заявок текущего фильтра (для «Запланировать» — нужен текст/сообщество).
let _adRequestsById = {};

document.addEventListener('DOMContentLoaded', async () => {
    await loadAdTemplates();
    await loadOfferImages();
    await loadAdRequests();
    const sel = document.getElementById('filter-status');
    if (sel) sel.addEventListener('change', loadAdRequests);
    const selOrigin = document.getElementById('filter-origin');
    if (selOrigin) selOrigin.addEventListener('change', loadAdRequests);
    // Делегированное удаление картинок (имена могут содержать пробелы/кириллицу).
    const box = document.getElementById('offer-images');
    if (box) box.addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-del-offer');
        if (btn) deleteOfferImage(btn.dataset.name);
    });

    // Массовые действия: отслеживаем чекбоксы карточек (делегирование).
    const list = document.getElementById('ad-list');
    if (list) list.addEventListener('change', (e) => {
        if (e.target.classList.contains('ad-check')) updateBulkBar();
    });
    const selAll = document.getElementById('bulk-select-all');
    if (selAll) selAll.addEventListener('click', () => {
        document.querySelectorAll('.ad-check').forEach(c => { c.checked = true; });
        updateBulkBar();
    });
    const clr = document.getElementById('bulk-clear');
    if (clr) clr.addEventListener('click', () => {
        document.querySelectorAll('.ad-check').forEach(c => { c.checked = false; });
        updateBulkBar();
    });
});

// --------------------------------------------------- массовые действия

function getSelectedIds() {
    return Array.from(document.querySelectorAll('.ad-check:checked'))
        .map(c => parseInt(c.value, 10))
        .filter(n => !isNaN(n));
}

function updateBulkBar() {
    const ids = getSelectedIds();
    const bar = document.getElementById('bulk-bar');
    const cnt = document.getElementById('bulk-count');
    if (cnt) cnt.textContent = ids.length;
    if (bar) bar.style.display = ids.length ? '' : 'none';
}

function _bulkRes(html, cls) {
    const el = document.getElementById('bulk-res');
    if (el) el.innerHTML = `<span class="text-${cls || 'muted'}">${html}</span>`;
}

async function bulkSet(status) {
    const ids = getSelectedIds();
    if (!ids.length) return;
    _bulkRes('Применяю…');
    try {
        const res = await apiClient.bulkAdAction(ids, 'status', status);
        _bulkRes(`Обновлено: ${res.affected}`, 'success');
        await loadAdRequests();
        updateBulkBar();
    } catch (e) {
        _bulkRes(`Ошибка: ${escapeHtml(e.message)}`, 'danger');
    }
}

async function bulkDelete() {
    const ids = getSelectedIds();
    if (!ids.length) return;
    if (!confirm(`Удалить выбранные заявки (${ids.length})? Действие необратимо.`)) return;
    _bulkRes('Удаляю…');
    try {
        const res = await apiClient.bulkAdAction(ids, 'delete');
        _bulkRes(`Удалено: ${res.affected}`, 'success');
        await loadAdRequests();
        updateBulkBar();
    } catch (e) {
        _bulkRes(`Ошибка: ${escapeHtml(e.message)}`, 'danger');
    }
}

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
        const originEl = document.getElementById('filter-origin');
        const origin = originEl ? originEl.value : '';
        // Инбокс кабинета — только реклама (route='ad_cabinet'). Не-рекламные ЛС,
        // отданные в уведомления, сюда не попадают (Этап 1 — единый роутер ЛС).
        const data = await apiClient.getAdRequests({ status, origin, route: 'ad_cabinet' });
        const requests = data.requests || [];
        loading.style.display = 'none';
        if (!requests.length) {
            empty.style.display = '';
            return;
        }
        _adRequestsById = {};
        requests.forEach(r => { _adRequestsById[r.id] = r; });
        list.innerHTML = requests.map(renderCard).join('');
    } catch (e) {
        loading.style.display = 'none';
        list.innerHTML = `<div class="alert alert-danger">Ошибка загрузки: ${escapeHtml(e.message)}</div>`;
    }
}

// Достижимость ЛС (Раунд 4): VK не разрешает сообществу писать этому автору
// (can_message=False, precheck со скана). Только для людей с валидным peer —
// автор-группа и нерезолвимый peer недостижимы по другой причине (нет личного
// диалога вовсе). Закрытым показываем бейдж + deeplink «ответить из личного VK».
function reachClosed(ar) {
    return ar.can_message === false && !ar.author_is_group && Number(ar.peer_id) > 0;
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

    const isDm = ar.origin === 'inbound_dm';
    const originBadge = ORIGIN_BADGE[ar.origin] || '';
    // Источник: для предложки — ссылка на пост; для лички — ссылка на диалог.
    const sourceLink = isDm
        ? (ar.dialog_url
            ? `<a href="${escapeHtml(ar.dialog_url)}" target="_blank"><i class="bi bi-box-arrow-up-right"></i> диалог в VK</a>`
            : 'входящее ЛС')
        : `<a href="${escapeHtml(ar.vk_post_url || '#')}" target="_blank">пост в VK</a>`;

    return `
    <div class="card mb-3" id="ad-card-${ar.id}">
        <div class="card-body">
            <div class="d-flex justify-content-between align-items-start mb-2">
                <div class="d-flex">
                    <input type="checkbox" class="form-check-input ad-check me-2 mt-1"
                           value="${ar.id}" title="Выбрать для массового действия">
                    <div>
                        <div class="fw-bold">${authorLink}</div>
                        <div class="text-muted small">
                            в «${escapeHtml(ar.community_name || '')}» · ${sourceLink}
                        </div>
                    </div>
                </div>
                <div class="text-end">
                    ${originBadge} ${STATUS_BADGE[ar.status] || escapeHtml(ar.status)}
                    <div class="mt-1">${scoreBadge(ar.score)}</div>
                    ${reachClosed(ar) ? `<div class="mt-1"><span class="badge bg-danger"
                        title="VK не разрешает сообществу писать этому автору в ЛС — отвечайте из личного VK (кнопка ниже)">
                        <i class="bi bi-envelope-slash"></i> ЛС закрыта</span></div>` : ''}
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
                    <i class="bi bi-send"></i> ${isDm ? 'Ответить в диалог' : 'Отправить от сообщества'}
                </button>
                ${reachClosed(ar) ? `<a class="btn btn-sm btn-warning"
                        href="https://vk.com/im?sel=${encodeURIComponent(ar.peer_id)}" target="_blank"
                        title="ЛС от сообщества закрыта. Откройте личный диалог и вставьте текст (кнопка «Копировать»).">
                        <i class="bi bi-box-arrow-up-right"></i> Ответить из личного VK
                    </a>` : ''}
                <button class="btn btn-sm btn-outline-secondary" onclick="copyCard(${ar.id})">
                    <i class="bi bi-clipboard"></i> Копировать
                </button>
                ${isDm
                    ? `<button class="btn btn-sm btn-outline-info" onclick="toggleThread(${ar.id})">
                           <i class="bi bi-chat-left-text"></i> Показать переписку
                       </button>
                       <button class="btn btn-sm btn-outline-warning" onclick="moveToNotifications(${ar.id})"
                               title="Это не реклама — перенести в раздел «Уведомления»">
                           <i class="bi bi-bell"></i> Не реклама → в уведомления
                       </button>`
                    : `<button class="btn btn-sm btn-success" onclick="openAccept(${ar.id})"
                               title="Оформить одной кнопкой: клиент + размещение (цена/срок) + ответ">
                           <i class="bi bi-check2-all"></i> Оформить
                       </button>
                       <button class="btn btn-sm btn-outline-success" onclick="spOpenFromRequest(${ar.id})"
                               title="Опубликовать САМ предложенный пост с подписью автора по расписанию + репост в соседей">
                           <i class="bi bi-people"></i> В планировщик предложки
                       </button>
                       <button class="btn btn-sm btn-outline-primary" onclick="scheduleFromRequest(${ar.id})">
                           <i class="bi bi-calendar-plus"></i> Запланировать
                       </button>
                       <button class="btn btn-sm btn-info" onclick="publishNow(${ar.id})"
                               title="Опубликовать бесплатно СЕЙЧАС (для бытовых объявлений) и убрать карточку">
                           <i class="bi bi-send-check"></i> Опубликовать
                       </button>
                       <button class="btn btn-sm btn-danger" onclick="deleteCard(${ar.id})"
                               title="Удалить пост из предложки VK И убрать из кабинета (в отличие от «Пропустить», который оставляет пост в VK)">
                           <i class="bi bi-trash"></i> Удалить
                       </button>`}
                <button class="btn btn-sm btn-outline-success" onclick="markCard(${ar.id}, 'published')">
                    <i class="bi bi-check2"></i> Опубликовано
                </button>
                <button class="btn btn-sm btn-outline-dark" onclick="markCard(${ar.id}, 'skipped')">
                    <i class="bi bi-x"></i> Пропустить
                </button>
                <button class="btn btn-sm btn-outline-secondary" onclick="addToCrm(${ar.id})"
                        title="Завести/привязать клиента в CRM по автору заявки">
                    <i class="bi bi-person-plus"></i> В CRM
                </button>
            </div>
            ${isDm ? `<div class="mt-2" id="thread-${ar.id}" style="display:none;"></div>` : ''}
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

// Опубликовать заявку бесплатно и сейчас (бытовые объявления) — пост в живой
// паблик + снять оригинал + убрать карточку. Подтверждаем (наружу-действие).
async function publishNow(id) {
    if (!confirm('Опубликовать это объявление в сообществе сейчас (бесплатно) и убрать из входящих?')) {
        return;
    }
    try {
        const r = await apiClient.publishAdRequestNow(id);
        const url = r && r.url ? ` (${escapeHtml(r.url)})` : '';
        _res(id, 'Опубликовано' + url, 'success');
        setTimeout(loadAdRequests, 500);
    } catch (e) {
        _res(id, 'Ошибка публикации: ' + escapeHtml(e.message), 'danger');
    }
}

// Удалить пост-предложку из VK И из кабинета (в отличие от «Пропустить», который
// оставляет пост висеть в VK). Наружу-действие + необратимо → подтверждаем.
async function deleteCard(id) {
    if (!confirm('Удалить этот пост из предложки VK и убрать из кабинета? Пост в VK будет удалён безвозвратно.')) {
        return;
    }
    _res(id, 'Удаляю из VK…', 'muted');
    try {
        const r = await apiClient.deleteAdRequestPost(id);
        _res(id, (r && r.already) ? 'Уже было удалено ранее.' : 'Удалено из VK и кабинета ✓', 'success');
        setTimeout(loadAdRequests, 500);
    } catch (e) {
        _res(id, 'Не удалось удалить из VK: ' + escapeHtml(e.message), 'danger');
    }
}

// R3: «Не реклама → в уведомления». Переносит ЛС-заявку в раздел «Уведомления»
// (route='notifications', наш статус обработки сбрасывается в new), и она уходит
// из инбокса кабинета.
async function moveToNotifications(id) {
    _res(id, 'Переношу в уведомления…', 'muted');
    try {
        await apiClient.setAdRoute(id, 'notifications');
        _res(id, 'Перенесено в «Уведомления» ✓', 'success');
        setTimeout(loadAdRequests, 500);
    } catch (e) {
        _res(id, 'Не удалось перенести: ' + escapeHtml(e.message), 'danger');
    }
}

// Завести/привязать клиента CRM (блок C) по автору заявки (author_vk_id).
async function addToCrm(id) {
    _res(id, 'Заношу в CRM…', 'muted');
    try {
        const res = await apiClient.upsertCrmFromRequest(id);
        const name = (res.client && res.client.name) || 'клиент';
        _res(id,
            (res.created ? 'Клиент заведён' : 'Привязано к существующему клиенту') +
            ` «${escapeHtml(name)}» — <a href="/ad#crm" target="_blank">открыть CRM</a>.`,
            'success');
    } catch (e) {
        _res(id, 'Не удалось завести клиента: ' + escapeHtml(e.message), 'danger');
    }
}

// --------------------------------------------------- тред-вью переписки (блок A)

async function toggleThread(id) {
    const box = document.getElementById(`thread-${id}`);
    if (!box) return;
    // Повторный клик — свернуть.
    if (box.style.display !== 'none' && box.dataset.loaded === '1') {
        box.style.display = 'none';
        box.dataset.loaded = '0';
        return;
    }
    box.style.display = '';
    box.innerHTML = '<div class="text-muted small">Загрузка переписки…</div>';
    try {
        const data = await apiClient.getAdThread(id);
        const msgs = data.messages || [];
        if (!msgs.length) {
            const why = data.reason === 'no_token' ? 'нет VK-токена'
                : data.reason === 'error' ? 'ошибка VK'
                : 'переписка пуста или недоступна';
            box.innerHTML = `<div class="text-muted small">История недоступна (${why}).</div>`;
            box.dataset.loaded = '1';
            return;
        }
        box.innerHTML = renderThread(msgs);
        box.dataset.loaded = '1';
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">Ошибка загрузки переписки: ${escapeHtml(e.message)}</div>`;
    }
}

function _fmtMsgDate(unix) {
    if (!unix) return '';
    try {
        return new Date(unix * 1000).toLocaleString('ru-RU', {
            day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
        });
    } catch (e) { return ''; }
}

function renderThread(msgs) {
    const rows = msgs.map(m => {
        const mine = m.out;  // true — написали мы (сообщество)
        const who = mine
            ? '<i class="bi bi-building"></i> мы'
            : `<i class="bi bi-person"></i> ${escapeHtml(m.from_name || 'автор')}`;
        const att = m.attachments
            ? ` <span class="badge bg-light text-dark border">+${m.attachments} вложений</span>` : '';
        const align = mine ? 'text-end' : '';
        const bg = mine ? 'bg-primary bg-opacity-10' : 'bg-light';
        return `<div class="${align} mb-1">
            <div class="d-inline-block border rounded p-2 ${bg}" style="max-width:85%;text-align:left;white-space:pre-wrap;">
                <div class="text-muted" style="font-size:.75em;">${who} · ${_fmtMsgDate(m.date)}</div>
                ${escapeHtml(m.text || '')}${att}
            </div>
        </div>`;
    }).join('');
    return `<div class="border rounded p-2 bg-white" style="max-height:280px;overflow:auto;">${rows}</div>`;
}

// =================================================== планировщик отложки

const _schCommunityNames = {};  // vk_group_id -> name (для подписи в таблице)
let _schInited = false;
let _schSourceRequestId = null;  // B2: из какой заявки предложки планируем
let _schSourceClientId = null;   // C: связанный CRM-клиент заявки (если заведён)

// B2: «Запланировать» на карточке заявки — открыть планировщик с префиллом.
// VK не даёт править предложку in-place (wall.edit 15/27), поэтому создаём
// новый отложенный пост, а оригинал убираем (опц.) на сабмите.
async function scheduleFromRequest(id) {
    const ar = _adRequestsById[id];
    if (!ar) return;
    const body = document.getElementById('scheduler-body');
    await initScheduler();  // идемпотентно; грузит список сообществ

    // Выбрать сообщество заявки (если его нет в списке — добавить опцию).
    const sel = document.getElementById('sch-community');
    if (sel) {
        const val = String(ar.community_vk_id);
        if (!Array.from(sel.options).some(o => o.value === val)) {
            const opt = document.createElement('option');
            opt.value = val;
            opt.textContent = ar.community_name || val;
            if (ar.region_id) opt.dataset.regionId = ar.region_id;
            sel.appendChild(opt);
        }
        sel.value = val;
    }
    const ta = document.getElementById('sch-text');
    if (ta) ta.value = ar.text_snapshot || '';

    _schSourceRequestId = id;
    _schSourceClientId = ar.client_id || null;
    const src = document.getElementById('sch-source');
    if (src) src.style.display = '';
    // Режим «из заявки»: заявочный текст плашки + строка «убрать оригинал» видимы
    // (клиентский режим И5 их прячет — восстанавливаем на каждый вход).
    const reqText = document.getElementById('sch-source-text-req');
    if (reqText) reqText.style.display = '';
    const cliText = document.getElementById('sch-source-text-client');
    if (cliText) cliText.style.display = 'none';
    const rmRow = document.getElementById('sch-source-remove-row');
    if (rmRow) rmRow.style.display = '';
    const lbl = document.getElementById('sch-source-label');
    if (lbl) lbl.innerHTML = ar.vk_post_url
        ? `<a href="${escapeHtml(ar.vk_post_url)}" target="_blank">#${id}</a>` : `#${id}`;
    const rm = document.getElementById('sch-remove-original');
    if (rm) rm.checked = true;
    const note = document.getElementById('sch-client-note');
    if (note) note.innerHTML = _schSourceClientId
        ? '<span class="text-success"><i class="bi bi-person-check"></i> Заявка привязана к клиенту CRM — сделка двинется в «Запланировано».</span>'
        : '<span class="text-muted"><i class="bi bi-info-circle"></i> Клиент в CRM не заведён. Нажмите «В CRM» на карточке заявки, чтобы привязать оплату/публикацию.</span>';

    // Единый кабинет (С1): переключиться на вкладку планировщика; в legacy-вёрстке
    // (collapse-секция на одной странице) — раскрыть её. Скролл — в обоих случаях.
    const schTabBtn = document.getElementById('tab-scheduler-btn');
    if (schTabBtn && window.bootstrap) {
        bootstrap.Tab.getOrCreateInstance(schTabBtn).show();
    } else if (body && window.bootstrap) {
        bootstrap.Collapse.getOrCreateInstance(body).show();
    }
    if (body) body.scrollIntoView({ behavior: 'smooth', block: 'start' });
    _schRes('Заявка перенесена в планировщик — задайте даты и отправьте.', 'muted');
}

function clearScheduleSource() {
    _schSourceRequestId = null;
    _schSourceClientId = null;
    const src = document.getElementById('sch-source');
    if (src) src.style.display = 'none';
    const note = document.getElementById('sch-client-note');
    if (note) note.innerHTML = '';
}

// И5 «всё в карточке»: «Запланировать публикацию» из карточки клиента CRM.
// Открывает тот же планировщик с зашитым client_id, но БЕЗ заявки-источника
// (нет предложки → нет «убрать оригинал»). Бэкенд /scheduled уже принимает
// client_id и двигает сделку в «Запланировано» — отдельный endpoint не нужен.
// Имя клиента берём из открытой карточки (#cf-name-<id>) — без экранирования в
// inline-onclick. Функция глобальна, зовётся из ad_crm.js (общий scope страницы).
async function scheduleForClient(clientId) {
    const nameEl = document.getElementById(`cf-name-${clientId}`);
    const clientName = nameEl ? (nameEl.value || '').trim() : '';
    const body = document.getElementById('scheduler-body');
    await initScheduler();  // идемпотентно; грузит список сообществ

    _schSourceRequestId = null;       // клиентский режим: заявки-источника нет
    _schSourceClientId = clientId;

    const src = document.getElementById('sch-source');
    if (src) src.style.display = '';
    // Клиентский режим плашки: прячем заявочный текст и «убрать оригинал»,
    // показываем клиентскую подпись.
    const reqText = document.getElementById('sch-source-text-req');
    if (reqText) reqText.style.display = 'none';
    const cliText = document.getElementById('sch-source-text-client');
    if (cliText) cliText.style.display = '';
    const cliLbl = document.getElementById('sch-source-client-label');
    if (cliLbl) cliLbl.textContent = clientName ? `«${clientName}»` : `#${clientId}`;
    const rmRow = document.getElementById('sch-source-remove-row');
    if (rmRow) rmRow.style.display = 'none';
    const rm = document.getElementById('sch-remove-original');
    if (rm) rm.checked = false;
    const note = document.getElementById('sch-client-note');
    if (note) note.innerHTML =
        '<span class="text-success"><i class="bi bi-person-check"></i> '
        + 'Публикация привяжется к клиенту — сделка двинется в «Запланировано».</span>';

    const schTabBtn = document.getElementById('tab-scheduler-btn');
    if (schTabBtn && window.bootstrap) {
        bootstrap.Tab.getOrCreateInstance(schTabBtn).show();
    } else if (body && window.bootstrap) {
        bootstrap.Collapse.getOrCreateInstance(body).show();
    }
    if (body) body.scrollIntoView({ behavior: 'smooth', block: 'start' });
    _schRes('Планируем за клиента — выберите сообщество, текст, картинки и даты, затем отправьте.', 'muted');
}

// ----------------------------------------------------------------------
// С5: сквозное оформление заявки одной кнопкой («Оформить»).
// ----------------------------------------------------------------------
let _acceptId = null;

function openAccept(id) {
    const ar = _adRequestsById[id];
    if (!ar) return;
    _acceptId = id;
    const info = document.getElementById('acc-info');
    if (info) info.textContent =
        `${ar.author_name || 'без имени'} → «${ar.community_name || ''}»`;
    const prep = document.getElementById(`prep-${id}`);
    const reply = document.getElementById('acc-reply');
    if (reply) reply.value = prep ? (prep.value || '').trim() : '';
    ['acc-price', 'acc-expire-days', 'acc-date'].forEach((k) => {
        const el = document.getElementById(k);
        if (el) el.value = '';
    });
    const res = document.getElementById('acc-res');
    if (res) res.innerHTML = '';
    if (window.bootstrap) {
        bootstrap.Modal.getOrCreateInstance(document.getElementById('accept-modal')).show();
    }
}

async function submitAccept() {
    if (!_acceptId) return;
    const res = document.getElementById('acc-res');
    const dateVal = document.getElementById('acc-date').value;
    if (!dateVal) {
        if (res) res.innerHTML = '<span class="text-danger">Укажите дату публикации.</span>';
        return;
    }
    const priceRaw = document.getElementById('acc-price').value;
    const daysRaw = document.getElementById('acc-expire-days').value;
    const replyRaw = (document.getElementById('acc-reply').value || '').trim();
    const payload = {
        dates: [dateVal],
        price: priceRaw ? parseFloat(priceRaw) : null,
        expire_days: daysRaw ? parseInt(daysRaw, 10) : null,
        from_group: document.getElementById('acc-from-group').checked,
        signed: document.getElementById('acc-signed').checked,
        comments_enabled: document.getElementById('acc-comments').checked,
        remove_original: document.getElementById('acc-remove').checked,
        reply_message: replyRaw || null,
    };
    const btn = document.getElementById('acc-submit');
    if (btn) btn.disabled = true;
    if (res) res.innerHTML = '<span class="text-muted">Оформляю…</span>';
    try {
        const r = await apiClient.acceptAdRequest(_acceptId, payload);
        let msg = `Запланировано: ${r.scheduled}` + (r.failed ? `, с ошибкой: ${r.failed}` : '');
        if (r.original_removed) msg += '; оригинал убран';
        if (r.reply && r.reply.success) msg += '; ответ отправлен';
        if (res) res.innerHTML = `<span class="text-success">${msg} ✓</span>`;
        await loadAdRequests();
        if (window.bootstrap) {
            setTimeout(() => bootstrap.Modal.getOrCreateInstance(
                document.getElementById('accept-modal')).hide(), 1300);
        }
    } catch (e) {
        if (res) res.innerHTML = `<span class="text-danger">Ошибка: ${escapeHtml(e.message)}</span>`;
    } finally {
        if (btn) btn.disabled = false;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const addBtn = document.getElementById('sch-add-date');
    if (addBtn) addBtn.addEventListener('click', () => addDateRow());
    const submitBtn = document.getElementById('sch-submit');
    if (submitBtn) submitBtn.addEventListener('click', submitSchedule);
    const refreshBtn = document.getElementById('sch-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', loadSchedule);
    // Удаление строки-даты (делегирование).
    const datesBox = document.getElementById('sch-dates');
    if (datesBox) datesBox.addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-del-date');
        if (btn) btn.closest('.sch-date-row').remove();
    });
    // Ленивая инициализация при первом раскрытии секции (не грузим регионы зря).
    const body = document.getElementById('scheduler-body');
    if (body) body.addEventListener('shown.bs.collapse', initScheduler, { once: true });
});

async function initScheduler() {
    if (_schInited) return;
    _schInited = true;
    await loadTargetCommunities();
    if (!document.querySelector('#sch-dates .sch-date-row')) addDateRow();
    await loadSchedule();
    spInit();
    spLoadMatrix();
}

// ─── Планировщик предложки (Этап 0, план 2026-09-05) ────────────────────
// Оригинал = сам предложенный пост с подписью автора (VK wall.post post_id,
// signed=1) по расписанию; дублёры = wall.repost оригинала от имени их
// сообщества в момент его выхода (диспетчер раз в минуту). Цену считает
// сервер: пол за КАЖДОЕ размещение, пусто = пол × число размещений.

let _spOptions = null;   // ответ /suggested-plan/options для выбранного сообщества
const _spMode = { vk_postpone: 'VK-отложка', queue: 'очередь SETKA' };

function _spName(gid) {
    const g = parseInt(gid, 10);
    return _schCommunityNames[g] || _schCommunityNames[-g] || _schCommunityNames[Math.abs(g)] || String(gid);
}

function spInit() {
    const sel = document.getElementById('sp-community');
    if (!sel) return;
    const src = document.getElementById('sch-community');
    // Тот же список сообществ, что у обычного планировщика (регионы с группой).
    const opts = src ? Array.from(src.options).filter(o => o.value) : [];
    sel.innerHTML = '<option value="">— выберите сообщество —</option>' +
        opts.map(o => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.textContent)}</option>`).join('');
}

function _spSetRes(html, cls) {
    const el = document.getElementById('sp-res');
    if (el) el.innerHTML = `<span class="text-${cls || 'muted'}">${html}</span>`;
}

async function spLoadOptions() {
    const sel = document.getElementById('sp-community');
    const list = document.getElementById('sp-list');
    const dups = document.getElementById('sp-dups');
    const gid = sel ? parseInt(sel.value, 10) : NaN;
    const hintEl = document.getElementById('sp-live-hint');
    if (hintEl) hintEl.innerHTML = '';  // подсказка прошлого сообщества не должна пережить смену выбора
    if (!gid) {
        _spOptions = null;
        if (list) list.innerHTML = '<span class="text-muted small">Сначала выберите сообщество.</span>';
        if (dups) dups.innerHTML = '';
        return;
    }
    if (list) list.innerHTML = '<span class="text-muted small">Загрузка предложки…</span>';
    try {
        _spOptions = await apiClient.getSuggestedPlanOptions(gid);
    } catch (e) {
        _spOptions = null;
        if (list) list.innerHTML = `<span class="text-danger small">Ошибка: ${escapeHtml(e.message)}</span>`;
        return;
    }
    const badge = document.getElementById('sp-mode-badge');
    if (badge) badge.textContent = _spMode[_spOptions.mode] || _spOptions.mode || '';
    const hint = document.getElementById('sp-live-hint');
    if (hint) {
        const lv = _spOptions.live || {};
        hint.innerHTML = lv.error
            ? `<span class="text-warning">⚠️ ${lv.error.includes('токен') ? escapeHtml(lv.error) + ' (проверьте /tokens)' : 'VK не ответил (' + escapeHtml(lv.error) + ')'} — показаны заявки из базы</span>`
            : `Предложка VK сейчас: <b>${lv.fetched >= 100 ? '100+' : (lv.fetched || 0)}</b> пост(ов)` +
              (lv.inserted ? `, новых заявок заведено: <b>${lv.inserted}</b>` : '') +
              (lv.revived ? `, вернулось из «исчез»: <b>${lv.revived}</b>` : '');
    }
    spRenderDups();
    spRenderCards();
}

function spRenderDups() {
    const box = document.getElementById('sp-dups');
    if (!box || !_spOptions) return;
    const cands = _spOptions.dup_candidates || [];
    if (!cands.length) {
        box.innerHTML = '<span class="text-muted">Дублировать некуда: других сообществ с группой нет.</span>';
        return;
    }
    box.innerHTML = cands.map(c =>
        `<label class="form-check form-check-inline border rounded px-2 py-1 m-0 ${c.default ? 'bg-primary bg-opacity-10' : ''}"
                title="${escapeHtml(c.code)}${c.default ? ' · сосед' : ''}">
            <input class="form-check-input sp-dup" type="checkbox" value="${c.community_vk_id}"
                   data-default="${c.default ? 1 : 0}" ${c.default ? 'checked' : ''} onchange="spUpdatePriceHints()">
            <span class="form-check-label">${escapeHtml(c.name)}</span>
        </label>`
    ).join('');
}

function spDupsSet(mode) {
    document.querySelectorAll('#sp-dups .sp-dup').forEach(cb => {
        cb.checked = mode === 'all' ? true : mode === 'none' ? false : cb.dataset.default === '1';
    });
    spUpdatePriceHints();
}

function _spSelectedDups() {
    return Array.from(document.querySelectorAll('#sp-dups .sp-dup:checked')).map(cb => parseInt(cb.value, 10));
}

function _spDefaultWhen() {
    // Завтра 10:00 по МСК как стартовая подсказка; оператор правит руками.
    const d = new Date(Date.now() + 24 * 3600 * 1000);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T10:00`;
}

function spUpdatePriceHints() {
    if (!_spOptions) return;
    const n = 1 + _spSelectedDups().length;
    const floor = _spOptions.floor_rub || 200;
    document.querySelectorAll('#sp-list .sp-price').forEach(inp => {
        inp.placeholder = `мин. ${n}×${floor} = ${n * floor} ₽`;
    });
    const hint = document.getElementById('sp-hint');
    if (hint) hint.innerHTML =
        `Размещений на пост: <b>${n}</b> (оригинал + ${n - 1} дублёров). ` +
        `Цена — за весь заказ, пусто = минимум ${n * floor} ₽. ` +
        'Репост уходит только после того, как оригинал реально вышел (±5 мин).';
}

function spRenderCards() {
    const list = document.getElementById('sp-list');
    if (!list || !_spOptions) return;
    const reqs = _spOptions.requests || [];
    if (!reqs.length) {
        list.innerHTML = '<span class="text-muted small">В предложке этого сообщества нет новых постов ' +
            '(или они уже обработаны во «Входящих»).</span>';
        return;
    }
    const when = _spDefaultWhen();
    list.innerHTML = reqs.map(ar => {
        const photos = (ar.photo_urls_json || []).slice(0, 8).map(u =>
            `<a href="${escapeHtml(u)}" target="_blank"><img src="${escapeHtml(u)}" style="height:64px;border-radius:6px;margin:2px;" loading="lazy"></a>`
        ).join('');
        const author = ar.author_url
            ? `<a href="${escapeHtml(ar.author_url)}" target="_blank">${escapeHtml(ar.author_name || 'без имени')}</a>`
            : escapeHtml(ar.author_name || 'без имени');
        return `
        <div class="border rounded p-2 mb-2" id="sp-item-${ar.id}">
            <div class="d-flex flex-wrap align-items-start gap-2">
                <input type="checkbox" class="form-check-input mt-1 sp-check" id="sp-check-${ar.id}" value="${ar.id}">
                <div class="flex-grow-1" style="min-width:260px;">
                    <div><i class="bi bi-person"></i> ${author}
                        <span class="text-muted small">· <a href="${escapeHtml(ar.vk_post_url || '#')}" target="_blank">пост в предложке</a>
                        · подпись «Предложил(а): ${escapeHtml(ar.author_name || '')}» сохранится</span></div>
                    <div class="border rounded p-2 mt-1 bg-light small" style="white-space:pre-wrap;max-height:200px;overflow:auto;">${escapeHtml(ar.text_snapshot || '')}</div>
                    ${photos ? `<div class="mt-1">${photos}</div>` : ''}
                </div>
                <div style="min-width:220px;">
                    <label class="form-label small mb-0">Выход (МСК)</label>
                    <input type="datetime-local" class="form-control form-control-sm sp-when" id="sp-when-${ar.id}" value="${when}">
                    <label class="form-label small mb-0 mt-1">Цена заказа, ₽</label>
                    <input type="number" min="0" step="1" class="form-control form-control-sm sp-price" id="sp-price-${ar.id}" placeholder="">
                    <div class="small mt-1" id="sp-res-${ar.id}"></div>
                </div>
            </div>
        </div>`;
    }).join('');
    spUpdatePriceHints();
}

async function spOpenFromRequest(id) {
    const ar = _adRequestsById[id];
    if (!ar) return;
    await initScheduler();
    const tabBtn = document.getElementById('tab-scheduler-btn');
    if (tabBtn && window.bootstrap) bootstrap.Tab.getOrCreateInstance(tabBtn).show();
    const sel = document.getElementById('sp-community');
    if (sel) {
        const val = String(ar.community_vk_id);
        const abs = String(Math.abs(ar.community_vk_id));
        const opt = Array.from(sel.options).find(o => o.value === val || o.value === abs || o.value === '-' + abs);
        if (opt) sel.value = opt.value;
        else {
            const o = document.createElement('option');
            o.value = val; o.textContent = ar.community_name || val;
            sel.appendChild(o); sel.value = val;
        }
    }
    await spLoadOptions();
    const cb = document.getElementById(`sp-check-${id}`);
    if (cb) cb.checked = true;
    const card = document.getElementById(`sp-item-${id}`) || document.getElementById('sp-card');
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function spSubmit() {
    if (!_spOptions) { _spSetRes('Сначала выберите сообщество.', 'danger'); return; }
    const checked = Array.from(document.querySelectorAll('#sp-list .sp-check:checked')).map(cb => parseInt(cb.value, 10));
    if (!checked.length) { _spSetRes('Отметьте хотя бы один предложенный пост.', 'danger'); return; }
    const dups = _spSelectedDups();
    const items = [];
    for (const id of checked) {
        const when = (document.getElementById(`sp-when-${id}`) || {}).value;
        if (!when) { _spSetRes(`Укажите дату выхода для поста #${id}.`, 'danger'); return; }
        const priceRaw = (document.getElementById(`sp-price-${id}`) || {}).value;
        items.push({
            request_id: id,
            publish_at: when,
            price: priceRaw ? parseFloat(priceRaw) : null,
            dup_community_ids: dups,
        });
    }
    const n = 1 + dups.length;
    if (!confirm(`Запланировать ${items.length} пост(ов) × ${n} размещений?\n` +
        (_spOptions.mode === 'vk_postpone'
            ? 'Оригинал уйдёт в VK-отложку с подписью автора; репосты выйдут вслед за ним.'
            : 'Оригинал опубликует SETKA в назначенное время; репосты — вслед за ним.'))) return;
    const btn = document.getElementById('sp-submit');
    if (btn) btn.disabled = true;
    _spSetRes('Планирую…', 'muted');
    try {
        const res = await apiClient.createSuggestedPlan({ community_vk_id: _spOptions.region.community_vk_id, items });
        let ok = 0;
        (res.items || []).forEach(it => {
            const el = document.getElementById(`sp-res-${it.request_id}`);
            if (it.ok && !it.already) {
                ok += 1;
                const reposts = (it.reposts || []).length;
                if (el) el.innerHTML = `<span class="text-success"><i class="bi bi-check2"></i> запланировано: оригинал + ${reposts} репост(ов), ${it.price_total ? it.price_total + ' ₽' : ''}</span>`;
                const cb = document.getElementById(`sp-check-${it.request_id}`);
                if (cb) { cb.checked = false; cb.disabled = true; }
            } else if (it.already) {
                if (el) el.innerHTML = '<span class="text-muted">уже запланировано ранее</span>';
            } else if (el) {
                el.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle"></i> ${escapeHtml(it.error || 'ошибка')}</span>`;
            }
        });
        _spSetRes(`Готово: ${ok} из ${items.length}.`, ok ? 'success' : 'danger');
        await spLoadMatrix();
        await loadSchedule();
        if (typeof loadAdRequests === 'function') loadAdRequests();
    } catch (e) {
        _spSetRes('Ошибка: ' + escapeHtml(e.message), 'danger');
    } finally {
        if (btn) btn.disabled = false;
    }
}

const SP_REPOST_BADGE = {
    scheduled: '<span class="badge bg-warning text-dark">ждёт выхода оригинала</span>',
    published: '<span class="badge bg-success">вышел</span>',
    failed: '<span class="badge bg-danger">ошибка</span>',
    cancelled: '<span class="badge bg-dark">отменён</span>',
};

function _spRepostChip(r) {
    const name = escapeHtml(_spName(r.community_vk_id));
    const badge = SP_REPOST_BADGE[r.status] || escapeHtml(r.status);
    const link = r.vk_post_url && r.status === 'published'
        ? `<a href="${escapeHtml(r.vk_post_url)}" target="_blank" title="открыть репост"><i class="bi bi-box-arrow-up-right"></i></a>` : '';
    const attempts = r.attempts ? `<span class="text-muted">· попытка ${r.attempts}</span>` : '';
    const err = r.error_message ? ` title="${escapeHtml(r.error_message)}"` : '';
    const cancel = r.status === 'scheduled'
        ? `<button class="btn btn-link btn-sm p-0 ms-1 text-danger" onclick="spCancel(${r.id}, 'repost')" title="Отменить репост"><i class="bi bi-x"></i></button>` : '';
    return `<div class="border rounded px-2 py-1"${err}>${name} ${badge} ${link} ${attempts} ${cancel}</div>`;
}

async function spLoadMatrix() {
    const box = document.getElementById('sp-matrix');
    if (!box) return;
    try {
        const data = await apiClient.getSuggestedPlan({});
        const plans = data.plans || [];
        if (!plans.length) {
            box.innerHTML = '<span class="text-muted">Планов из предложки пока нет.</span>';
            return;
        }
        box.innerHTML = plans.map(p => {
            const o = p.original;
            const name = escapeHtml(_spName(o.community_vk_id));
            const link = o.vk_post_url
                ? `<a href="${escapeHtml(o.vk_post_url)}" target="_blank">${name}</a>` : name;
            const text = (o.text || '').slice(0, 90);
            const st = SCH_STATUS_BADGE[o.status] || escapeHtml(o.status);
            const queue = o.status === 'scheduled' && o.next_attempt_at
                ? '<span class="badge bg-info text-dark">очередь SETKA</span>' : '';
            const err = o.error_message ? `<div class="text-danger" style="font-size:.85em;">${escapeHtml(o.error_message)}</div>` : '';
            const cancel = (o.status === 'scheduled' || o.status === 'draft')
                ? `<button class="btn btn-sm btn-outline-danger py-0 px-1" onclick="spCancel(${o.id}, 'suggested')" title="Отменить оригинал и невышедшие репосты"><i class="bi bi-x"></i></button>` : '';
            const reposts = (p.reposts || []).map(_spRepostChip).join('');
            const price = (o.price != null ? Number(o.price) : 0) + (p.reposts || []).reduce((s, r) => s + (r.price != null ? Number(r.price) : 0), 0);
            return `<div class="border rounded p-2 mb-2">
                <div class="d-flex flex-wrap align-items-center gap-2">
                    <span class="text-nowrap"><i class="bi bi-calendar-event"></i> ${_fmtSchedDate(o.publish_date)}</span>
                    <span><b>${link}</b> ${st} ${queue}</span>
                    <span class="text-muted">${escapeHtml(text)}${(o.text || '').length > 90 ? '…' : ''}</span>
                    <span class="ms-auto text-nowrap">${price ? price.toFixed(0) + ' ₽' : ''} ${cancel}</span>
                </div>
                ${err}
                <div class="d-flex flex-wrap gap-1 mt-1">${reposts || '<span class="text-muted">без дублёров</span>'}</div>
            </div>`;
        }).join('');
    } catch (e) {
        box.innerHTML = `<span class="text-danger">Ошибка: ${escapeHtml(e.message)}</span>`;
    }
}

async function spCancel(id, kind) {
    const msg = kind === 'suggested'
        ? 'Отменить оригинал? В режиме VK-отложки пост уйдёт из «Отложенных» и предложка НЕ восстановится; ' +
          'все ещё не вышедшие репосты будут отменены.'
        : 'Отменить этот репост?';
    if (!confirm(msg)) return;
    try {
        const res = await apiClient.cancelScheduledPost(id);
        if (res.cancel_error) alert('Не отменено: ' + res.cancel_error);
        await spLoadMatrix();
        await loadSchedule();
    } catch (e) {
        alert('Ошибка отмены: ' + e.message);
    }
}

async function loadTargetCommunities() {
    const sel = document.getElementById('sch-community');
    if (!sel) return;
    try {
        const regions = await apiClient.getRegions();
        const withGroup = (regions || []).filter(r => r.vk_group_id);
        if (!withGroup.length) {
            sel.innerHTML = '<option value="">— нет сообществ с VK-группой —</option>';
            return;
        }
        withGroup.forEach(r => { _schCommunityNames[r.vk_group_id] = r.name || r.code; });
        sel.innerHTML = withGroup.map(r =>
            `<option value="${r.vk_group_id}" data-region-id="${r.id}">${escapeHtml(r.name || r.code)}</option>`
        ).join('');
    } catch (e) {
        sel.innerHTML = `<option value="">Ошибка: ${escapeHtml(e.message)}</option>`;
    }
}

function addDateRow(value) {
    const box = document.getElementById('sch-dates');
    if (!box) return;
    const row = document.createElement('div');
    row.className = 'sch-date-row input-group input-group-sm mb-1';
    row.style.maxWidth = '320px';
    row.innerHTML =
        `<input type="datetime-local" class="form-control sch-date" value="${value || ''}">` +
        `<button type="button" class="btn btn-outline-danger btn-del-date" title="Убрать дату">` +
        `<i class="bi bi-x"></i></button>`;
    box.appendChild(row);
}

function collectScheduleDates() {
    return Array.from(document.querySelectorAll('#sch-dates .sch-date'))
        .map(i => i.value)
        .filter(v => v);
}

function _schRes(html, cls) {
    const el = document.getElementById('sch-res');
    if (el) el.innerHTML = `<span class="text-${cls || 'muted'}">${html}</span>`;
}

async function submitSchedule() {
    const sel = document.getElementById('sch-community');
    const opt = sel && sel.selectedOptions[0];
    const communityVkId = opt ? parseInt(opt.value, 10) : NaN;
    if (!communityVkId) { _schRes('Выберите сообщество.', 'danger'); return; }
    const dates = collectScheduleDates();
    if (!dates.length) { _schRes('Добавьте хотя бы одну дату публикации.', 'danger'); return; }
    const text = (document.getElementById('sch-text').value || '').trim();
    const images = selectedOfferImages();
    if (!text && !images.length) {
        _schRes('Пустой пост: нужен текст или отмеченные картинки.', 'danger');
        return;
    }

    const removeOriginal = _schSourceRequestId
        && document.getElementById('sch-remove-original')
        && document.getElementById('sch-remove-original').checked;
    const priceEl = document.getElementById('sch-price');
    const priceRaw = priceEl ? priceEl.value : '';
    const expDaysEl = document.getElementById('sch-expire-days');
    const expDaysRaw = expDaysEl ? expDaysEl.value : '';
    const expAtEl = document.getElementById('sch-expire-at');
    const expAtRaw = expAtEl ? expAtEl.value : '';
    const payload = {
        community_vk_id: communityVkId,
        region_id: opt.dataset.regionId ? parseInt(opt.dataset.regionId, 10) : null,
        text,
        images,
        dates,
        from_group: document.getElementById('sch-from-group').checked,
        signed: document.getElementById('sch-signed').checked,
        comments_enabled: document.getElementById('sch-comments').checked,
        source_ad_request_id: _schSourceRequestId || null,
        remove_original: !!removeOriginal,
        client_id: _schSourceClientId || null,
        price: priceRaw ? parseFloat(priceRaw) : null,
        expire_days: expDaysRaw ? parseInt(expDaysRaw, 10) : null,
        expire_at: expAtRaw || null,
        // Ключ идемпотентности: повторный клик/ретрай с тем же ключом не создаст вторую раскладку.
        client_ref: (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()),
    };
    _schRes('Планирую…');
    const btn = document.getElementById('sch-submit');
    if (btn) btn.disabled = true;
    try {
        const res = await apiClient.createScheduledPosts(payload);
        let msg = `Запланировано: ${res.scheduled}` + (res.failed ? `, с ошибкой: ${res.failed}` : '');
        if (payload.remove_original) {
            msg += res.original_removed
                ? '; оригинал убран из предложки, заявка → опубликовано'
                : (res.original_remove_error
                    ? `; оригинал убрать не удалось (${escapeHtml(res.original_remove_error)})`
                    : '');
        }
        if (res.client_id) msg += '; сделка двинута в «Запланировано»';
        _schRes(msg + ' ✓', res.failed ? 'warning' : 'success');
        // Очищаем даты и цену — они привязаны к конкретной сделке; текст/картинки
        // оставляем для следующей раскладки.
        document.getElementById('sch-dates').innerHTML = '';
        addDateRow();
        const priceEl2 = document.getElementById('sch-price');
        if (priceEl2) priceEl2.value = '';
        if (expDaysEl) expDaysEl.value = '';
        if (expAtEl) expAtEl.value = '';
        // Заявка отработана — отвязываем источник и обновляем инбокс.
        if (_schSourceRequestId) { clearScheduleSource(); await loadAdRequests(); }
        await loadSchedule();
    } catch (e) {
        _schRes('Ошибка: ' + escapeHtml(e.message), 'danger');
    } finally {
        if (btn) btn.disabled = false;
    }
}

const SCH_STATUS_BADGE = {
    draft: '<span class="badge bg-secondary">черновик</span>',
    scheduled: '<span class="badge bg-primary">в отложке</span>',
    published: '<span class="badge bg-success">опубликовано</span>',
    failed: '<span class="badge bg-danger">ошибка</span>',
    cancelled: '<span class="badge bg-dark">отменено</span>',
};

function _fmtSchedDate(iso) {
    // publish_date — МСК wall-clock; показываем как есть (без TZ-сдвига).
    return iso ? String(iso).replace('T', ' ').slice(0, 16) : '';
}

async function loadSchedule() {
    const box = document.getElementById('sch-list');
    if (!box) return;
    box.innerHTML = '<span class="text-muted">Загрузка…</span>';
    try {
        const data = await apiClient.getScheduledPosts({});
        const rows = data.scheduled || [];
        if (!rows.length) {
            box.innerHTML = '<span class="text-muted">Пока ничего не запланировано.</span>';
            return;
        }
        box.innerHTML =
            '<table class="table table-sm align-middle"><thead><tr>' +
            '<th>Дата (МСК)</th><th>Сообщество</th><th>Текст</th><th>Статус</th><th></th>' +
            '</tr></thead><tbody>' + rows.map(renderScheduledRow).join('') + '</tbody></table>';
    } catch (e) {
        box.innerHTML = `<span class="text-danger">Ошибка: ${escapeHtml(e.message)}</span>`;
    }
}

function renderScheduledRow(r) {
    const full = r.text || '';
    const preview = escapeHtml(full.slice(0, 60)) + (full.length > 60 ? '…' : '');
    const name = _schCommunityNames[r.community_vk_id] || String(r.community_vk_id);
    const community = r.vk_post_url
        ? `<a href="${escapeHtml(r.vk_post_url)}" target="_blank">${escapeHtml(name)}</a>`
        : escapeHtml(name);
    const canCancel = r.status === 'scheduled' || r.status === 'draft';
    const cancelBtn = canCancel
        ? `<button class="btn btn-sm btn-outline-danger py-0 px-1" onclick="cancelSchedule(${r.id})" title="Отменить"><i class="bi bi-x"></i></button>`
        : '';
    // Планировщик предложки: вид строки — оригинал с подписью или репост-дублёр.
    const kindBadge = r.kind === 'suggested'
        ? ' <span class="badge bg-info text-dark" title="сам предложенный пост с подписью автора">предложка</span>'
        : r.kind === 'repost'
            ? ' <span class="badge bg-light text-dark border" title="репост оригинала после его выхода">репост</span>'
            : '';
    const err = r.error_message
        ? `<div class="text-danger" style="font-size:.8em;">${escapeHtml(r.error_message)}</div>` : '';
    const expiry = r.expires_at
        ? `<div class="text-muted" style="font-size:.8em;"><i class="bi bi-clock-history"></i> снять ${_fmtSchedDate(r.expires_at)}</div>`
        : '';
    return `<tr>
        <td class="text-nowrap">${_fmtSchedDate(r.publish_date)}${expiry}</td>
        <td>${community}${kindBadge}</td>
        <td>${preview}${err}</td>
        <td>${SCH_STATUS_BADGE[r.status] || escapeHtml(r.status)}</td>
        <td>${cancelBtn}</td>
    </tr>`;
}

async function cancelSchedule(id) {
    if (!confirm('Отменить запланированный пост (убрать из VK-отложки)?')) return;
    try {
        const res = await apiClient.cancelScheduledPost(id);
        if (res.cancel_error) {
            alert('VK не дал удалить пост: ' + res.cancel_error);
        }
        await loadSchedule();
    } catch (e) {
        alert('Ошибка отмены: ' + escapeHtml(e.message));
    }
}
