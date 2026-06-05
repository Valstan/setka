// CRM рекламного кабинета (блок C): клиенты, воронка, оплаты, публикации.

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const STAGE_LABEL = {
    detected: 'Обнаружен',
    contacted: 'Связались',
    scheduled: 'Запланировано',
    published: 'Опубликовано',
    paid: 'Оплачено',
    lost: 'Слив',
};

const STAGE_BADGE_CLASS = {
    detected: 'bg-warning text-dark',
    contacted: 'bg-info text-dark',
    scheduled: 'bg-primary',
    published: 'bg-success',
    paid: 'bg-success',
    lost: 'bg-secondary',
};

const STAGE_ORDER = ['detected', 'contacted', 'scheduled', 'published', 'paid', 'lost'];

function fmtMoney(n) {
    const v = Number(n || 0);
    return v.toLocaleString('ru-RU') + ' ₽';
}

function fmtDate(iso) {
    return iso ? String(iso).replace('T', ' ').slice(0, 16) : '';
}

let _filterTimer = null;
let _banks = [];        // фикс-список банков (PR-3)
let _bankStats = [];    // частоты «куда чаще платят»

document.addEventListener('DOMContentLoaded', () => {
    loadBanks();
    loadCrm();
    const stageSel = document.getElementById('filter-stage');
    if (stageSel) stageSel.addEventListener('change', loadClients);
    const q = document.getElementById('filter-q');
    if (q) q.addEventListener('input', () => {
        clearTimeout(_filterTimer);
        _filterTimer = setTimeout(loadClients, 350);
    });
    const submit = document.getElementById('nc-submit');
    if (submit) submit.addEventListener('click', createClientFromModal);
});

async function loadCrm() {
    await Promise.all([loadFunnel(), loadClients()]);
}

// Банки (PR-3): фикс-список + частоты. Грузим один раз для дропдаунов оплаты.
async function loadBanks() {
    try {
        const d = await apiClient.getCrmBanks();
        _banks = d.banks || [];
        _bankStats = d.stats || [];
    } catch (e) {
        _banks = [];
    }
}

function bankSelectHtml(id, current) {
    const opts = ['<option value="">— банк —</option>'].concat(
        _banks.map(b => `<option value="${escapeHtml(b)}"${b === current ? ' selected' : ''}>${escapeHtml(b)}</option>`)
    ).join('');
    return `<select class="form-select" id="${id}" style="max-width:130px;">${opts}</select>`;
}

// --------------------------------------------------- воронка

async function loadFunnel() {
    const box = document.getElementById('crm-funnel');
    if (!box) return;
    try {
        const f = await apiClient.getCrmFunnel();
        const chips = STAGE_ORDER.map(st => {
            const n = (f.by_stage && f.by_stage[st]) || 0;
            const cls = STAGE_BADGE_CLASS[st] || 'bg-light text-dark';
            return `<div class="text-center px-3 py-2 rounded border">
                <div class="h4 mb-0">${n}</div>
                <span class="badge ${cls}">${STAGE_LABEL[st]}</span>
            </div>`;
        }).join('');
        const awaiting = Number(f.total_awaiting || 0);
        const awaitingChip = awaiting > 0 ? `<div class="text-center px-3 py-2 rounded border bg-body-tertiary">
                <div class="h4 mb-0 text-warning">${fmtMoney(awaiting)}</div>
                <div class="small text-muted">ждём оплату</div>
            </div>` : '';
        const totals = `<div class="text-center px-3 py-2 rounded border bg-body-tertiary ms-auto">
                <div class="h4 mb-0 text-success">${fmtMoney(f.total_paid)}</div>
                <div class="small text-muted">оплачено всего</div>
            </div>
            ${awaitingChip}
            <div class="text-center px-3 py-2 rounded border bg-body-tertiary">
                <div class="h4 mb-0">${f.publications_count || 0}</div>
                <div class="small text-muted">публикаций</div>
            </div>`;
        box.innerHTML = chips + totals;
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">Ошибка воронки: ${escapeHtml(e.message)}</div>`;
    }
}

// --------------------------------------------------- список клиентов

async function loadClients() {
    const loading = document.getElementById('crm-loading');
    const empty = document.getElementById('crm-empty');
    const list = document.getElementById('crm-list');
    loading.style.display = '';
    empty.style.display = 'none';
    list.innerHTML = '';
    try {
        const stage = document.getElementById('filter-stage').value;
        const q = document.getElementById('filter-q').value.trim();
        const data = await apiClient.getCrmClients({ stage, q });
        const clients = data.clients || [];
        loading.style.display = 'none';
        if (!clients.length) {
            empty.style.display = '';
            return;
        }
        list.innerHTML = clients.map(renderClientCard).join('');
    } catch (e) {
        loading.style.display = 'none';
        list.innerHTML = `<div class="alert alert-danger">Ошибка загрузки: ${escapeHtml(e.message)}</div>`;
    }
}

function stageSelectHtml(id, current) {
    const opts = STAGE_ORDER.map(st =>
        `<option value="${st}"${st === current ? ' selected' : ''}>${STAGE_LABEL[st]}</option>`
    ).join('');
    return `<select class="form-select form-select-sm" style="width:auto;display:inline-block;"
                    onchange="changeStage(${id}, this.value)">${opts}</select>`;
}

function renderClientCard(c) {
    const nameLabel = c.author_is_group
        ? `<i class="bi bi-people"></i> ${escapeHtml(c.name || 'сообщество')}`
        : `<i class="bi bi-person"></i> ${escapeHtml(c.name || 'без имени')}`;
    const nameLink = c.vk_url
        ? `<a href="${escapeHtml(c.vk_url)}" target="_blank">${nameLabel}</a>` : nameLabel;
    const contact = c.contact
        ? `<div class="text-muted small"><i class="bi bi-telephone"></i> ${escapeHtml(c.contact)}</div>` : '';

    return `
    <div class="card mb-2" id="crm-card-${c.id}">
        <div class="card-body">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <div class="fw-bold">${nameLink}</div>
                    ${contact}
                    <div class="small mt-1">
                        <span class="text-success fw-bold">${fmtMoney(c.total_paid)}</span>
                        ${Number(c.total_awaiting || 0) > 0
                            ? `<span class="badge bg-warning text-dark" title="Ожидает оплаты">⏳ ${fmtMoney(c.total_awaiting)}</span>` : ''}
                        <span class="text-muted">· ${c.payments_count || 0} оплат · ${c.publications_count || 0} публикаций</span>
                    </div>
                </div>
                <div class="text-end">
                    ${stageSelectHtml(c.id, c.stage)}
                </div>
            </div>
            <div class="mt-2">
                <button class="btn btn-sm btn-outline-secondary" onclick="toggleClientDetails(${c.id})">
                    <i class="bi bi-chevron-down"></i> Подробнее
                </button>
            </div>
            <div id="crm-details-${c.id}" class="mt-3" style="display:none;"></div>
        </div>
    </div>`;
}

async function changeStage(id, stage) {
    try {
        await apiClient.updateCrmClient(id, { stage });
        await loadFunnel();
    } catch (e) {
        alert('Не удалось сменить стадию: ' + e.message);
    }
}

// --------------------------------------------------- карточка: детали

async function toggleClientDetails(id) {
    const box = document.getElementById(`crm-details-${id}`);
    if (!box) return;
    if (box.style.display !== 'none' && box.dataset.loaded === '1') {
        box.style.display = 'none';
        box.dataset.loaded = '0';
        return;
    }
    box.style.display = '';
    box.innerHTML = '<div class="text-muted small">Загрузка…</div>';
    try {
        const d = await apiClient.getCrmClient(id);
        box.innerHTML = renderClientDetails(d);
        box.dataset.loaded = '1';
        loadClientThread(id);
        loadOrderItems(id);
        loadTimeline(id);
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

function renderClientDetails(d) {
    const c = d.client;
    const payments = d.payments || [];
    const publications = d.publications || [];

    const payRows = payments.length ? payments.map(p => {
        const awaiting = p.status === 'awaiting';
        const amountCls = awaiting ? 'text-warning' : 'text-success';
        const statusBadge = awaiting
            ? '<span class="badge bg-warning text-dark">ждём</span>'
            : '<span class="badge bg-success">оплачено</span>';
        const markPaidBtn = awaiting
            ? `<button class="btn btn-sm btn-outline-success py-0 px-1" title="Отметить оплаченной"
                       onclick="markPaid(${p.id}, ${c.id})"><i class="bi bi-check2"></i></button>` : '';
        return `
        <tr>
            <td class="text-nowrap fw-bold ${amountCls}">${fmtMoney(p.amount)}</td>
            <td>${statusBadge}</td>
            <td class="small text-nowrap">${escapeHtml(p.bank || '')}</td>
            <td class="text-nowrap small">${fmtDate(p.paid_at)}</td>
            <td class="small">${escapeHtml(p.note || p.method || '')}</td>
            <td class="text-nowrap">
                ${markPaidBtn}
                <button class="btn btn-sm btn-outline-secondary py-0 px-1" title="Правка"
                        onclick="editPayment(${p.id}, ${c.id}, ${p.amount})"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-outline-danger py-0 px-1"
                        onclick="deletePayment(${p.id}, ${c.id})" title="Удалить"><i class="bi bi-x"></i></button>
            </td>
        </tr>`;
    }).join('') : '<tr><td colspan="6" class="text-muted small">Оплат пока нет.</td></tr>';

    const pubRows = publications.length ? publications.map(p => {
        const link = p.vk_post_url
            ? `<a href="${escapeHtml(p.vk_post_url)}" target="_blank">${escapeHtml(String(p.community_vk_id))}</a>`
            : escapeHtml(String(p.community_vk_id));
        return `<tr>
            <td class="text-nowrap">${link}</td>
            <td class="text-nowrap">${p.price != null ? fmtMoney(p.price) : ''}</td>
            <td class="text-nowrap small">${fmtDate(p.published_at)}</td>
            <td class="small">${escapeHtml(p.status || '')} ${escapeHtml(p.note || '')}</td>
            <td><button class="btn btn-sm btn-outline-danger py-0 px-1"
                        onclick="deletePublication(${p.id}, ${c.id})" title="Удалить"><i class="bi bi-x"></i></button></td>
        </tr>`;
    }).join('') : '<tr><td colspan="5" class="text-muted small">Публикаций пока нет.</td></tr>';

    return `
    <div class="row g-3">
        <div class="col-md-5">
            <label class="form-label small mb-1">Имя / название</label>
            <input type="text" class="form-control form-control-sm mb-2" id="cf-name-${c.id}" value="${escapeHtml(c.name || '')}">
            <label class="form-label small mb-1">Контакты</label>
            <textarea class="form-control form-control-sm mb-2" id="cf-contact-${c.id}" rows="2">${escapeHtml(c.contact || '')}</textarea>
            <label class="form-label small mb-1">Заметки</label>
            <textarea class="form-control form-control-sm mb-2" id="cf-notes-${c.id}" rows="2">${escapeHtml(c.notes || '')}</textarea>
            <div class="d-flex gap-2">
                <button class="btn btn-sm btn-outline-primary" onclick="saveClientFields(${c.id})">
                    <i class="bi bi-save"></i> Сохранить
                </button>
                <button class="btn btn-sm btn-outline-danger ms-auto" onclick="deleteClient(${c.id})">
                    <i class="bi bi-trash"></i> Удалить клиента
                </button>
            </div>
            <div class="small mt-1" id="cf-res-${c.id}"></div>
        </div>

        <div class="col-md-7">
            <div class="fw-bold small mb-1"><i class="bi bi-cash-coin"></i> Оплаты</div>
            <table class="table table-sm align-middle mb-2">
                <tbody>${payRows}</tbody>
            </table>
            <div class="input-group input-group-sm mb-1">
                <input type="number" class="form-control" id="pay-amount-${c.id}" placeholder="Сумма ₽" style="max-width:100px;">
                ${bankSelectHtml(`pay-bank-${c.id}`, '')}
                <input type="text" class="form-control" id="pay-note-${c.id}" placeholder="Заметка">
                <button class="btn btn-outline-success" onclick="addPayment(${c.id})"><i class="bi bi-plus-lg"></i> Оплата</button>
            </div>
            <div class="form-check form-check-inline small mb-3">
                <input class="form-check-input" type="checkbox" id="pay-awaiting-${c.id}">
                <label class="form-check-label text-muted" for="pay-awaiting-${c.id}">ожидание оплаты (деньги ещё не пришли)</label>
            </div>

            <div class="fw-bold small mb-1"><i class="bi bi-megaphone"></i> Публикации</div>
            <table class="table table-sm align-middle mb-2">
                <tbody>${pubRows}</tbody>
            </table>
            <div class="input-group input-group-sm">
                <input type="number" class="form-control" id="pub-comm-${c.id}" placeholder="VK id группы (-100…)" style="max-width:150px;">
                <input type="number" class="form-control" id="pub-post-${c.id}" placeholder="post id" style="max-width:90px;">
                <input type="number" class="form-control" id="pub-price-${c.id}" placeholder="Цена ₽" style="max-width:100px;">
                <button class="btn btn-outline-primary" onclick="addPublication(${c.id})"><i class="bi bi-plus-lg"></i> Публикация</button>
            </div>
        </div>
    </div>

    <hr class="my-3">
    <div class="fw-bold small mb-1"><i class="bi bi-chat-text"></i> Переписка с клиентом</div>
    <div id="crm-chat-${c.id}" class="border rounded p-2 mb-2"
         style="max-height:240px; overflow-y:auto; font-size:0.85rem;">
        <div class="text-muted small">Загрузка переписки…</div>
    </div>
    <div class="input-group input-group-sm mb-1">
        <input type="text" class="form-control" id="chat-msg-${c.id}" placeholder="Ответить клиенту от имени сообщества…"
               onkeydown="if(event.key==='Enter'){sendClientReply(${c.id});}">
        <button class="btn btn-outline-primary" onclick="sendClientReply(${c.id})"><i class="bi bi-send"></i> Отправить</button>
    </div>
    <div class="small" id="chat-status-${c.id}"></div>

    <hr class="my-3">
    <div class="d-flex justify-content-between align-items-center mb-1">
        <div class="fw-bold small"><i class="bi bi-card-checklist"></i> Заказ / размещения</div>
        <span class="small text-muted" id="order-total-${c.id}"></span>
    </div>
    <div id="crm-orders-${c.id}"><div class="text-muted small">Загрузка…</div></div>
    <div class="input-group input-group-sm mt-2">
        <input type="text" class="form-control" id="oi-desc-${c.id}" placeholder="Что за реклама (описание)">
        <input type="number" class="form-control" id="oi-qty-${c.id}" value="1" title="Количество" style="max-width:70px;">
        <input type="date" class="form-control" id="oi-from-${c.id}" title="Период с" style="max-width:140px;">
        <input type="date" class="form-control" id="oi-to-${c.id}" title="Период по" style="max-width:140px;">
        <button class="btn btn-outline-primary" onclick="addOrderItem(${c.id})" title="Добавить позицию"><i class="bi bi-plus-lg"></i></button>
    </div>
    <div class="input-group input-group-sm mt-1 mb-1">
        <span class="input-group-text">из заявки №</span>
        <input type="number" class="form-control" id="oi-req-${c.id}" placeholder="id заявки" style="max-width:120px;">
        <button class="btn btn-outline-secondary" onclick="addOrderItemFromRequest(${c.id})">Подтянуть</button>
    </div>

    <hr class="my-3">
    <div class="fw-bold small mb-1"><i class="bi bi-clock-history"></i> История взаимодействий</div>
    <div class="input-group input-group-sm mb-2">
        <input type="text" class="form-control" id="note-text-${c.id}"
               placeholder="Заметка: что обсудили / о чём договорились…"
               onkeydown="if(event.key==='Enter'){addNote(${c.id});}">
        <button class="btn btn-outline-secondary" onclick="addNote(${c.id})"><i class="bi bi-plus-lg"></i> Заметка</button>
    </div>
    <div id="crm-timeline-${c.id}"><div class="text-muted small">Загрузка истории…</div></div>`;
}

// --------------------------------------------------- таймлайн взаимодействий

// Вид события → иконка + подпись по умолчанию (если summary пуст).
const KIND_META = {
    reply_sent: { icon: 'bi-reply', label: 'Ответ клиенту' },
    status_changed: { icon: 'bi-flag', label: 'Смена статуса' },
    scheduled: { icon: 'bi-calendar-plus', label: 'Запланировано' },
    cancelled: { icon: 'bi-x-circle', label: 'Отмена отложки' },
    published: { icon: 'bi-megaphone', label: 'Публикация' },
    payment_added: { icon: 'bi-cash-coin', label: 'Оплата' },
    payment_paid: { icon: 'bi-check2-circle', label: 'Оплачено' },
    payment_deleted: { icon: 'bi-cash', label: 'Удалена оплата' },
    publication_deleted: { icon: 'bi-trash', label: 'Удалена публикация' },
    detected: { icon: 'bi-person-plus', label: 'Заведён клиент' },
    linked: { icon: 'bi-link-45deg', label: 'Привязана заявка' },
    contacted: { icon: 'bi-chat-dots', label: 'Контакт' },
    note: { icon: 'bi-sticky', label: 'Заметка' },
};

async function loadTimeline(id) {
    const box = document.getElementById(`crm-timeline-${id}`);
    if (!box) return;
    try {
        const d = await apiClient.getCrmTimeline(id);
        box.innerHTML = renderTimeline(d.timeline || [], id);
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">Ошибка истории: ${escapeHtml(e.message)}</div>`;
    }
}

function renderTimeline(events, clientId) {
    if (!events.length) return '<div class="text-muted small">Событий пока нет.</div>';
    const rows = events.map(ev => {
        const m = KIND_META[ev.kind] || { icon: 'bi-dot', label: ev.kind };
        const auto = ev.actor === 'system' ? ' · авто' : '';
        return `<li class="d-flex gap-2 align-items-start py-1 border-bottom">
            <i class="bi ${m.icon} text-secondary mt-1"></i>
            <div class="flex-grow-1">
                <div class="small">${escapeHtml(ev.summary || m.label)}</div>
                <div class="text-muted" style="font-size:0.72rem;">${fmtDate(ev.created_at)}${auto}</div>
            </div>
            <button class="btn btn-sm btn-outline-secondary py-0 px-1" title="Правка"
                    data-summary="${escapeHtml(ev.summary || '')}"
                    onclick="editInteraction(${ev.id}, ${clientId}, this)"><i class="bi bi-pencil"></i></button>
            <button class="btn btn-sm btn-outline-danger py-0 px-1" title="Удалить"
                    onclick="deleteInteraction(${ev.id}, ${clientId})"><i class="bi bi-x"></i></button>
        </li>`;
    }).join('');
    return `<ul class="list-unstyled mb-0">${rows}</ul>`;
}

async function addNote(id) {
    const el = document.getElementById(`note-text-${id}`);
    if (!el) return;
    const summary = (el.value || '').trim();
    if (!summary) return;
    try {
        await apiClient.createCrmInteraction({ client_id: id, summary });
        el.value = '';
        await loadTimeline(id);
    } catch (e) {
        alert('Не удалось добавить заметку: ' + e.message);
    }
}

async function editInteraction(id, clientId, btn) {
    const current = btn ? (btn.dataset.summary || '') : '';
    const next = prompt('Правка записи истории:', current);
    if (next === null) return;
    try {
        await apiClient.updateCrmInteraction(id, { summary: next });
        await loadTimeline(clientId);
    } catch (e) {
        alert('Не удалось изменить запись: ' + e.message);
    }
}

async function deleteInteraction(id, clientId) {
    if (!confirm('Удалить запись истории?')) return;
    try {
        await apiClient.deleteCrmInteraction(id);
        await loadTimeline(clientId);
    } catch (e) {
        alert('Не удалось удалить запись: ' + e.message);
    }
}

// --------------------------------------------------- заказ / размещения (PR-4)

const ORDER_STATUS = {
    planned: { label: 'план', cls: 'bg-secondary' },
    scheduled: { label: 'в отложке', cls: 'bg-primary' },
    published: { label: 'вышло', cls: 'bg-success' },
    cancelled: { label: 'отменено', cls: 'bg-light text-dark' },
};

async function loadOrderItems(id) {
    const box = document.getElementById(`crm-orders-${id}`);
    if (!box) return;
    try {
        const d = await apiClient.getCrmOrderItems(id);
        box.innerHTML = renderOrderItems(d.order_items || [], id);
        const total = document.getElementById(`order-total-${id}`);
        if (total) total.textContent = (d.total_quantity || 0) ? `${d.total_quantity} размещений` : '';
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">Ошибка заказа: ${escapeHtml(e.message)}</div>`;
    }
}

function orderStatusSelect(id, clientId, current) {
    const opts = Object.keys(ORDER_STATUS).map(st =>
        `<option value="${st}"${st === current ? ' selected' : ''}>${ORDER_STATUS[st].label}</option>`
    ).join('');
    return `<select class="form-select form-select-sm" style="width:auto;"
                    onchange="changeOrderStatus(${id}, ${clientId}, this.value)">${opts}</select>`;
}

function renderOrderItems(items, clientId) {
    if (!items.length) return '<div class="text-muted small">Позиций пока нет.</div>';
    const rows = items.map(it => {
        const period = (it.period_start || it.period_end)
            ? `${it.period_start || '…'} – ${it.period_end || '…'}` : '';
        const source = it.ad_request_id ? `из заявки №${it.ad_request_id}` : 'вручную';
        const meta = [period, source].filter(Boolean).join(' · ');
        return `<div class="d-flex gap-2 align-items-start py-1 border-bottom">
            <div class="flex-grow-1">
                <div class="small">${escapeHtml(it.description || '—')} <span class="text-muted">×${it.quantity}</span></div>
                <div class="text-muted" style="font-size:0.72rem;">${escapeHtml(meta)}</div>
            </div>
            ${orderStatusSelect(it.id, clientId, it.status)}
            <button class="btn btn-sm btn-outline-secondary py-0 px-1" title="Правка"
                    data-desc="${escapeHtml(it.description || '')}" data-qty="${it.quantity}"
                    onclick="editOrderItem(${it.id}, ${clientId}, this)"><i class="bi bi-pencil"></i></button>
            <button class="btn btn-sm btn-outline-danger py-0 px-1" title="Удалить"
                    onclick="deleteOrderItem(${it.id}, ${clientId})"><i class="bi bi-x"></i></button>
        </div>`;
    }).join('');
    return rows;
}

async function addOrderItem(id) {
    const description = document.getElementById(`oi-desc-${id}`).value.trim();
    const quantity = parseInt(document.getElementById(`oi-qty-${id}`).value, 10) || 1;
    const period_start = document.getElementById(`oi-from-${id}`).value || null;
    const period_end = document.getElementById(`oi-to-${id}`).value || null;
    if (!description) { alert('Опишите, что за реклама.'); return; }
    try {
        await apiClient.createCrmOrderItem({ client_id: id, description, quantity, period_start, period_end });
        document.getElementById(`oi-desc-${id}`).value = '';
        await loadOrderItems(id);
        await loadTimeline(id);
    } catch (e) {
        alert('Не удалось добавить позицию: ' + e.message);
    }
}

async function addOrderItemFromRequest(id) {
    const reqId = parseInt(document.getElementById(`oi-req-${id}`).value, 10);
    if (!reqId) { alert('Укажите id заявки.'); return; }
    try {
        await apiClient.createCrmOrderItemFromRequest(reqId);
        document.getElementById(`oi-req-${id}`).value = '';
        await loadOrderItems(id);
        await loadTimeline(id);
    } catch (e) {
        alert('Не удалось подтянуть из заявки: ' + e.message);
    }
}

async function changeOrderStatus(itemId, clientId, status) {
    try {
        await apiClient.updateCrmOrderItem(itemId, { status });
        await loadOrderItems(clientId);
    } catch (e) {
        alert('Не удалось сменить статус: ' + e.message);
    }
}

async function editOrderItem(itemId, clientId, btn) {
    const curDesc = btn ? (btn.dataset.desc || '') : '';
    const curQty = btn ? (btn.dataset.qty || '1') : '1';
    const description = prompt('Описание позиции:', curDesc);
    if (description === null) return;
    const qtyStr = prompt('Количество размещений:', curQty);
    if (qtyStr === null) return;
    const quantity = parseInt(qtyStr, 10) || 1;
    try {
        await apiClient.updateCrmOrderItem(itemId, { description, quantity });
        await loadOrderItems(clientId);
    } catch (e) {
        alert('Не удалось изменить позицию: ' + e.message);
    }
}

async function deleteOrderItem(itemId, clientId) {
    if (!confirm('Удалить позицию заказа?')) return;
    try {
        await apiClient.deleteCrmOrderItem(itemId);
        await loadOrderItems(clientId);
    } catch (e) {
        alert('Не удалось удалить позицию: ' + e.message);
    }
}

// --------------------------------------------------- чат с клиентом (PR-5)

const CHAT_REASON = {
    no_dialog: 'Диалога с клиентом нет (нет заявки с ЛС). Ответить можно после первого входящего сообщения.',
    no_token: 'Нет VK-токена для чтения переписки.',
    error: 'Не удалось загрузить переписку (VK недоступен).',
};

function fmtChatTime(unixSec) {
    if (!unixSec) return '';
    try { return new Date(unixSec * 1000).toLocaleString('ru-RU').slice(0, 16); }
    catch (e) { return ''; }
}

async function loadClientThread(id) {
    const box = document.getElementById(`crm-chat-${id}`);
    if (!box) return;
    try {
        const d = await apiClient.getCrmClientThread(id);
        if (d.reason) {
            box.innerHTML = `<div class="text-muted small">${escapeHtml(CHAT_REASON[d.reason] || d.reason)}</div>`;
            return;
        }
        box.innerHTML = renderChat(d.messages || []);
        box.scrollTop = box.scrollHeight;  // прокрутить к свежим
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">Ошибка переписки: ${escapeHtml(e.message)}</div>`;
    }
}

function renderChat(messages) {
    if (!messages.length) return '<div class="text-muted small">Сообщений пока нет.</div>';
    return messages.map(m => {
        const mine = m.out;
        const align = mine ? 'text-end' : 'text-start';
        const bubble = mine ? 'bg-primary text-white' : 'bg-body-secondary';
        const who = mine ? 'Мы' : escapeHtml(m.from_name || 'клиент');
        const att = m.attachments ? ` 📎×${m.attachments}` : '';
        return `<div class="${align} mb-1">
            <span class="d-inline-block rounded px-2 py-1 ${bubble}" style="max-width:85%; white-space:pre-wrap; text-align:left;">${escapeHtml(m.text || '')}${att}</span>
            <div class="text-muted" style="font-size:0.68rem;">${who} · ${fmtChatTime(m.date)}</div>
        </div>`;
    }).join('');
}

async function sendClientReply(id) {
    const el = document.getElementById(`chat-msg-${id}`);
    const status = document.getElementById(`chat-status-${id}`);
    if (!el) return;
    const message = (el.value || '').trim();
    if (!message) return;
    if (status) status.innerHTML = '<span class="text-muted">Отправляю…</span>';
    try {
        const res = await apiClient.sendCrmClientReply(id, message);
        if (res.success) {
            el.value = '';
            if (status) status.innerHTML = '<span class="text-success">Отправлено ✓</span>';
            await loadClientThread(id);
            await loadTimeline(id);
        } else if (res.allowed === false) {
            const link = res.personal_deeplink;
            if (status) status.innerHTML =
                `<span class="text-warning">VK не даёт писать первым. Откройте <a href="${link}" target="_blank">личный диалог</a> и ответьте с личного аккаунта.</span>`;
        } else {
            if (status) status.innerHTML = `<span class="text-danger">Ошибка: ${escapeHtml(res.error || 'не отправлено')}</span>`;
        }
    } catch (e) {
        if (status) status.innerHTML = `<span class="text-danger">Ошибка: ${escapeHtml(e.message)}</span>`;
    }
}

function _cfRes(id, html, cls) {
    const el = document.getElementById(`cf-res-${id}`);
    if (el) el.innerHTML = `<span class="text-${cls || 'muted'}">${html}</span>`;
}

async function saveClientFields(id) {
    const name = document.getElementById(`cf-name-${id}`).value;
    const contact = document.getElementById(`cf-contact-${id}`).value;
    const notes = document.getElementById(`cf-notes-${id}`).value;
    _cfRes(id, 'Сохраняю…');
    try {
        await apiClient.updateCrmClient(id, { name, contact, notes });
        _cfRes(id, 'Сохранено ✓', 'success');
        await loadClients();
    } catch (e) {
        _cfRes(id, 'Ошибка: ' + escapeHtml(e.message), 'danger');
    }
}

async function deleteClient(id) {
    if (!confirm('Удалить клиента вместе с его оплатами? Публикации останутся без привязки. Действие необратимо.')) return;
    try {
        await apiClient.deleteCrmClient(id);
        await loadCrm();
    } catch (e) {
        alert('Не удалось удалить: ' + e.message);
    }
}

// --------------------------------------------------- оплаты / публикации

async function addPayment(id) {
    const amount = parseFloat(document.getElementById(`pay-amount-${id}`).value);
    if (!amount || amount <= 0) { _detailReload(id, 'Введите сумму больше нуля.'); return; }
    const bank = document.getElementById(`pay-bank-${id}`).value || null;
    const note = document.getElementById(`pay-note-${id}`).value.trim() || null;
    const awaiting = document.getElementById(`pay-awaiting-${id}`).checked;
    try {
        await apiClient.createCrmPayment({
            client_id: id, amount, bank, note, status: awaiting ? 'awaiting' : 'paid',
        });
        await _detailReload(id);
        await loadFunnel();
    } catch (e) {
        alert('Не удалось добавить оплату: ' + e.message);
    }
}

async function markPaid(paymentId, clientId) {
    try {
        await apiClient.updateCrmPayment(paymentId, { status: 'paid' });
        await _detailReload(clientId);
        await loadFunnel();
    } catch (e) {
        alert('Не удалось отметить оплаченной: ' + e.message);
    }
}

async function editPayment(paymentId, clientId, currentAmount) {
    const next = prompt('Новая сумма оплаты, ₽:', currentAmount != null ? currentAmount : '');
    if (next === null) return;
    const amount = parseFloat(next);
    if (!amount || amount <= 0) { alert('Сумма должна быть больше нуля.'); return; }
    try {
        await apiClient.updateCrmPayment(paymentId, { amount });
        await _detailReload(clientId);
        await loadFunnel();
    } catch (e) {
        alert('Не удалось изменить оплату: ' + e.message);
    }
}

async function deletePayment(paymentId, clientId) {
    if (!confirm('Удалить оплату?')) return;
    try {
        await apiClient.deleteCrmPayment(paymentId);
        await _detailReload(clientId);
        await loadFunnel();
    } catch (e) {
        alert('Не удалось удалить оплату: ' + e.message);
    }
}

async function addPublication(id) {
    const community = parseInt(document.getElementById(`pub-comm-${id}`).value, 10);
    if (!community) { alert('Укажите VK id группы (отрицательный).'); return; }
    const postRaw = document.getElementById(`pub-post-${id}`).value;
    const priceRaw = document.getElementById(`pub-price-${id}`).value;
    const payload = {
        client_id: id,
        community_vk_id: community,
        vk_post_id: postRaw ? parseInt(postRaw, 10) : null,
        price: priceRaw ? parseFloat(priceRaw) : null,
    };
    try {
        await apiClient.createCrmPublication(payload);
        await _detailReload(id);
        await loadFunnel();
    } catch (e) {
        alert('Не удалось добавить публикацию: ' + e.message);
    }
}

async function deletePublication(pubId, clientId) {
    if (!confirm('Удалить запись о публикации?')) return;
    try {
        await apiClient.deleteCrmPublication(pubId);
        await _detailReload(clientId);
        await loadFunnel();
    } catch (e) {
        alert('Не удалось удалить публикацию: ' + e.message);
    }
}

// Перерисовать раскрытые детали клиента (после добавления/удаления записи).
async function _detailReload(id, errMsg) {
    const box = document.getElementById(`crm-details-${id}`);
    if (!box) return;
    try {
        const d = await apiClient.getCrmClient(id);
        box.innerHTML = renderClientDetails(d);
        box.dataset.loaded = '1';
        box.style.display = '';
        loadClientThread(id);
        loadOrderItems(id);
        loadTimeline(id);
        if (errMsg) _cfRes(id, escapeHtml(errMsg), 'danger');
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">Ошибка: ${escapeHtml(e.message)}</div>`;
    }
}

// --------------------------------------------------- модалка «Завести клиента»

async function createClientFromModal() {
    const vk = parseInt(document.getElementById('nc-vk-id').value, 10);
    const res = document.getElementById('nc-res');
    if (!vk) { res.innerHTML = '<span class="text-danger">Укажите VK id заказчика.</span>'; return; }
    const payload = {
        author_vk_id: vk,
        author_is_group: document.getElementById('nc-is-group').checked,
        name: document.getElementById('nc-name').value.trim() || null,
        contact: document.getElementById('nc-contact').value.trim() || null,
        stage: document.getElementById('nc-stage').value,
        notes: document.getElementById('nc-notes').value.trim() || null,
    };
    res.innerHTML = '<span class="text-muted">Создаю…</span>';
    try {
        await apiClient.createCrmClient(payload);
        res.innerHTML = '<span class="text-success">Создан ✓</span>';
        // Сброс полей + закрыть модалку.
        ['nc-vk-id', 'nc-name', 'nc-contact', 'nc-notes'].forEach(i => { document.getElementById(i).value = ''; });
        document.getElementById('nc-is-group').checked = false;
        const modalEl = document.getElementById('client-modal');
        if (window.bootstrap && modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).hide();
        await loadCrm();
    } catch (e) {
        const msg = e.message && e.message.includes('exists')
            ? 'Клиент с таким VK id уже есть.' : escapeHtml(e.message);
        res.innerHTML = `<span class="text-danger">Ошибка: ${msg}</span>`;
    }
}
