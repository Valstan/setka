/* Кабинет рекламодателя: онбординг, посты, заказ, оплата, чат.
 *
 * Все данные — /api/advertiser/* (изоляция по client_id на сервере).
 * Чат — polling 4 с c after_id, интервал живёт только при открытой вкладке
 * (паттерн radar.js). Цена — только с сервера (/quote), клиент её не считает.
 */
(function () {
    'use strict';

    var state = {
        me: null,
        regions: [],
        photos: [],
        chatLastId: 0,
        chatTimer: null,
    };

    function $(id) { return document.getElementById(id); }

    /* Кабинет клиента, открытый владельцем (?as_client=<id>).
     * Параметр прокидывается во ВСЕ запросы кабинета — иначе страница
     * показывала бы чужие данные вперемешку со своими: /me ушёл бы с
     * as_client, а /posts без него. Сервер решает, кто имеет право; здесь
     * только перенос. */
    var AS_CLIENT = '';
    try {
        AS_CLIENT = new URLSearchParams(location.search).get('as_client') || '';
    } catch (e) { /* старый браузер — работаем как обычный клиент */ }

    function withAsClient(url) {
        if (!AS_CLIENT) return url;
        return url + (url.indexOf('?') === -1 ? '?' : '&') + 'as_client=' + encodeURIComponent(AS_CLIENT);
    }

    async function api(path, opts) {
        var res = await fetch(withAsClient('/api/advertiser' + path), Object.assign({
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
        }, opts || {}));
        if (res.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); throw new Error('401'); }
        var data = null;
        try { data = await res.json(); } catch (e) { /* пустой ответ */ }
        if (!res.ok) throw new Error((data && data.detail) || ('HTTP ' + res.status));
        return data;
    }

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function fmtDate(iso) {
        if (!iso) return '';
        return iso.replace('T', ' ').slice(0, 16);
    }

    // ---------------- Вкладки ----------------

    document.querySelectorAll('#cab-tabs [data-tab]').forEach(function (a) {
        a.addEventListener('click', function () { showTab(a.dataset.tab); });
    });

    function showTab(tab) {
        document.querySelectorAll('#cab-tabs [data-tab]').forEach(function (a) {
            a.classList.toggle('active', a.dataset.tab === tab);
        });
        document.querySelectorAll('#cabinet [data-pane]').forEach(function (p) {
            p.classList.toggle('d-none', p.dataset.pane !== tab);
        });
        stopChatPolling();
        if (tab === 'posts') loadPosts();
        if (tab === 'new') loadNewPostPane();
        if (tab === 'money') loadMoney();
        if (tab === 'chat') { loadChat(true); startChatPolling(); }
    }

    // ---------------- Онбординг / профиль ----------------

    async function boot() {
        var me;
        try { me = await api('/me'); } catch (e) { return; }
        state.me = me;
        $('me-badge').textContent = me.display_name || '';

        if (me.impersonating) {
            $('owner-banner-name').textContent =
                (me.impersonating.name || '') + ' #' + me.impersonating.client_id;
            $('owner-banner').classList.remove('d-none');
        }

        if (!me.is_advertiser) {
            // Владельцу онбординг не предлагаем: своей карточки у него нет и
            // заводить её не надо — он входит в чужие кабинеты.
            if (me.is_owner) { showOwnerPicker(); return; }
            $('onboarding').classList.remove('d-none');
            return;
        }
        $('cabinet').classList.remove('d-none');
        loadPosts();
        refreshSummary();
    }

    async function showOwnerPicker() {
        var sel = $('owner-client-select');
        try {
            var data = await api('/clients');
            (data.clients || []).forEach(function (c) {
                var o = document.createElement('option');
                o.value = c.id;
                o.textContent = '#' + c.id + ' · ' + (c.name || 'без имени') +
                    (c.has_account ? '' : ' (без аккаунта)');
                sel.appendChild(o);
            });
        } catch (e) {
            sel.innerHTML = '<option value="">не удалось загрузить список</option>';
        }
        $('owner-picker').classList.remove('d-none');
    }

    $('owner-enter-btn').addEventListener('click', function () {
        var id = $('owner-client-select').value;
        if (id) location.href = '/cabinet?as_client=' + encodeURIComponent(id);
    });

    $('onb-submit').addEventListener('click', async function () {
        try {
            await api('/onboarding', {
                method: 'POST',
                body: JSON.stringify({ name: $('onb-name').value, phone: $('onb-phone').value }),
            });
            location.reload();
        } catch (e) { alert(e.message); }
    });

    $('logout-btn').addEventListener('click', function () {
        fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' })
            .then(function () { location.href = '/login'; });
    });

    async function refreshSummary() {
        try {
            var s = await api('/summary');
            var b = s.balance || {};
            $('balance-line').textContent =
                'Оплачено ' + (b.paid || 0) + ' ₽ · израсходовано ' + (b.spent || 0) +
                ' ₽ · запланировано ' + (s.planned_total || 0) + ' ₽';
            var badge = $('chat-badge');
            if (s.chat_unread > 0) {
                badge.textContent = s.chat_unread;
                badge.classList.remove('d-none');
            } else {
                badge.classList.add('d-none');
            }
            if (s.client && !s.client.trusted) $('np-moderation-note').classList.remove('d-none');
            renderPackage(s);
        } catch (e) { /* сводка не критична */ }
    }

    // Пакеты (заказ владельца 2026-08-26): плашка остатка / блокировки.
    function renderPackage(s) {
        var box = $('package-line');
        if (!box) return;
        if (s.package_block) {
            box.className = 'alert alert-warning py-2 small';
            box.textContent = '⛔ ' + s.package_block;
            box.classList.remove('d-none');
            return;
        }
        var p = s.package;
        if (p) {
            var kindName = { free_promo: 'Акция — бесплатные посты', prepaid: 'Оплаченный пакет', postpaid: 'Пакет с постоплатой' }[p.kind] || 'Пакет';
            box.className = 'alert alert-success py-2 small';
            box.textContent = '🎁 ' + kindName + ': осталось ' + p.posts_left + ' из ' + p.posts_total +
                (p.period_end ? ' до ' + p.period_end : '') +
                ' — посты в счёт пакета, без оплаты по прайсу';
            box.classList.remove('d-none');
            return;
        }
        var waiting = (s.packages || []).filter(function (x) {
            return x.is_active && x.kind === 'prepaid' && !x.paid;
        });
        if (waiting.length) {
            box.className = 'alert alert-info py-2 small';
            box.textContent = '⌛ Пакет ждёт подтверждения оплаты владельцем — после отметки посты пойдут в счёт пакета';
            box.classList.remove('d-none');
            return;
        }
        box.classList.add('d-none');
    }

    // ---------------- Мои посты ----------------

    function statusBadge(st) {
        var map = {
            pending: ['warning', 'на одобрении'],
            draft: ['secondary', 'черновик'],
            scheduled: ['info', 'запланирован'],
            published: ['success', 'опубликован'],
            failed: ['danger', 'ошибка'],
            cancelled: ['secondary', 'отменён'],
            rejected: ['danger', 'отклонён'],
        };
        var m = map[st] || ['secondary', st];
        return '<span class="badge text-bg-' + m[0] + '">' + esc(m[1]) + '</span>';
    }

    async function loadPosts() {
        var box = $('posts-list');
        box.innerHTML = '<div class="text-body-secondary">Загрузка…</div>';
        try {
            var data = await api('/posts');
            var items = [];
            (data.scheduled || []).forEach(function (p) {
                var lines = [
                    '<div class="card"><div class="card-body py-2">',
                    '<div class="d-flex justify-content-between align-items-center">',
                    '<div>' + statusBadge(p.status) +
                        ' <span class="small text-body-secondary">выход ' + esc(fmtDate(p.publish_date)) + ' МСК' +
                        (p.price != null ? ' · ' + p.price + ' ₽' : '') + '</span></div>',
                ];
                if (['pending', 'draft', 'scheduled'].indexOf(p.status) >= 0) {
                    lines.push('<button class="btn btn-sm btn-outline-danger" data-cancel="' + p.id + '">Отменить</button>');
                }
                lines.push('</div>');
                if (p.status === 'rejected' && p.moderation_comment) {
                    lines.push('<div class="small text-danger">Причина: ' + esc(p.moderation_comment) + '</div>');
                }
                if (p.status === 'failed' && p.error_message) {
                    lines.push('<div class="small text-danger">' + esc(p.error_message) + '</div>');
                }
                lines.push('<div class="cab-text small mt-1">' + esc((p.text || '').slice(0, 300)) + '</div>');
                lines.push('</div></div>');
                items.push(lines.join(''));
            });
            (data.publications || []).forEach(function (p) {
                items.push([
                    '<div class="card"><div class="card-body py-2">',
                    '<div class="d-flex justify-content-between align-items-center">',
                    '<div><span class="badge text-bg-success">вышел</span> ',
                    '<span class="small text-body-secondary">' + esc(fmtDate(p.published_at)) +
                        (p.price != null ? ' · ' + p.price + ' ₽' : '') +
                        ' · оплата: ' + (p.paid_status === 'paid' ? '✅' : '⏳') + '</span></div>',
                    p.vk_post_url ? '<a class="btn btn-sm btn-outline-secondary" target="_blank" href="' + esc(p.vk_post_url) + '">Открыть</a>' : '',
                    '</div>',
                    '<div class="small text-body-secondary mt-1">',
                    '👁 ' + (p.views == null ? '—' : p.views),
                    ' · ❤ ' + (p.likes == null ? '—' : p.likes),
                    ' · 🔁 ' + (p.reposts == null ? '—' : p.reposts),
                    ' · 💬 ' + (p.comments == null ? '—' : p.comments),
                    p.stats_updated_at ? ' <span class="ms-1">(обновлено ' + esc(fmtDate(p.stats_updated_at)) + ')</span>' : '',
                    '</div>',
                    '</div></div>',
                ].join(''));
            });
            box.innerHTML = items.length ? items.join('') :
                '<div class="text-body-secondary">Постов пока нет — создайте первый на вкладке «Новый пост».</div>';
            box.querySelectorAll('[data-cancel]').forEach(function (btn) {
                btn.addEventListener('click', async function () {
                    if (!confirm('Отменить пост?')) return;
                    try {
                        var r = await api('/posts/' + btn.dataset.cancel + '/cancel', { method: 'POST' });
                        if (r.cancel_error) alert('VK не дал снять пост: ' + r.cancel_error);
                        loadPosts();
                    } catch (e) { alert(e.message); }
                });
            });
        } catch (e) {
            box.innerHTML = '<div class="text-danger">' + esc(e.message) + '</div>';
        }
    }

    // ---------------- Новый пост ----------------

    async function loadNewPostPane() {
        refreshSummary();
        if (!state.regions.length) {
            try {
                var data = await api('/price-table');
                state.regions = data.regions || [];
            } catch (e) { alert(e.message); return; }
            var grid = $('np-regions');
            grid.innerHTML = state.regions.map(function (r) {
                return '<div class="form-check">' +
                    '<input class="form-check-input np-region" type="checkbox" value="' + r.id + '" id="reg-' + r.id + '">' +
                    '<label class="form-check-label small" for="reg-' + r.id + '">' + esc(r.name) + '</label></div>';
            }).join('');
            grid.addEventListener('change', updateQuote);
        }
        loadMyPhotos();
    }

    $('np-whole').addEventListener('change', function () {
        var whole = $('np-whole').checked;
        document.querySelectorAll('.np-region').forEach(function (cb) {
            cb.disabled = whole;
        });
        updateQuote();
    });

    function selectedRegions() {
        if ($('np-whole').checked) return state.regions.map(function (r) { return r.id; });
        return Array.prototype.map.call(
            document.querySelectorAll('.np-region:checked'),
            function (cb) { return parseInt(cb.value, 10); }
        );
    }

    var quoteTimer = null;
    function updateQuote() {
        clearTimeout(quoteTimer);
        quoteTimer = setTimeout(async function () {
            var ids = selectedRegions();
            if (!ids.length) { $('np-price').textContent = '0 ₽'; $('np-price-note').textContent = ''; return; }
            try {
                var q = await api('/quote', { method: 'POST', body: JSON.stringify({ region_ids: ids }) });
                if (q.blocked) {
                    $('np-price').textContent = '—';
                    $('np-price-note').textContent = '⛔ ' + q.blocked;
                    return;
                }
                if (q.package) {
                    $('np-price').textContent = '0 ₽';
                    $('np-price-note').textContent = q.over_limit
                        ? '⚠️ в пакете осталось ' + q.package.posts_left + ' постов — выберите меньше районов'
                        : '🎁 в счёт пакета (осталось ' + q.package.posts_left + ' из ' + q.package.posts_total + ')';
                    return;
                }
                $('np-price').textContent = q.price + ' ₽';
                $('np-price-note').textContent = q.anchor ? '(' + q.anchor + (q.saved ? ', выгода ' + q.saved + ' ₽' : '') + ')' : '';
            } catch (e) { $('np-price-note').textContent = e.message; }
        }, 250);
    }

    $('np-text').addEventListener('input', function () {
        $('np-text-count').textContent = $('np-text').value.length;
    });

    document.querySelectorAll('[name="np-when"]').forEach(function (r) {
        r.addEventListener('change', function () {
            $('np-datetime').disabled = $('np-now').checked;
        });
    });

    async function loadMyPhotos() {
        try {
            var data = await api('/photos');
            state.photos = (data.photos || []).map(function (p) { return p.name; });
            renderPhotos();
        } catch (e) { /* не критично */ }
    }

    function renderPhotos() {
        $('np-photos').innerHTML = state.photos.map(function (name) {
            return '<div class="position-relative">' +
                '<img class="cab-photo-thumb" src="/api/advertiser/photos/' + encodeURIComponent(name) + '">' +
                '<button class="btn-close position-absolute top-0 end-0 bg-white" data-del="' + esc(name) + '"></button>' +
                '</div>';
        }).join('');
        document.querySelectorAll('[data-del]').forEach(function (btn) {
            btn.addEventListener('click', async function () {
                await api('/photos/' + encodeURIComponent(btn.dataset.del), { method: 'DELETE' });
                loadMyPhotos();
            });
        });
    }

    $('np-file').addEventListener('change', async function () {
        var f = $('np-file').files[0];
        if (!f) return;
        var fd = new FormData();
        fd.append('file', f);
        try {
            var res = await fetch('/api/advertiser/photos', { method: 'POST', body: fd, credentials: 'same-origin' });
            var data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Ошибка загрузки');
            $('np-file').value = '';
            loadMyPhotos();
        } catch (e) { alert(e.message); }
    });

    $('np-submit').addEventListener('click', async function () {
        var body = {
            text: $('np-text').value,
            photos: state.photos,
            region_ids: $('np-whole').checked ? [] : selectedRegions(),
            whole_network: $('np-whole').checked,
            publish_now: $('np-now').checked,
            publish_at: $('np-now').checked ? null : ($('np-datetime').value || null),
        };
        $('np-submit').disabled = true;
        try {
            var r = await api('/orders', { method: 'POST', body: JSON.stringify(body) });
            alert(r.moderation
                ? 'Заказ создан (' + r.price_total + ' ₽) и отправлен на одобрение владельцу.'
                : 'Заказ создан: ' + r.price_total + ' ₽.');
            $('np-text').value = '';
            $('np-text-count').textContent = '0';
            showTab('posts');
        } catch (e) {
            alert(e.message);
        } finally {
            $('np-submit').disabled = false;
        }
    });

    // ---------------- Оплата ----------------

    async function loadMoney() {
        try {
            var s = await api('/summary');
            var b = s.balance || {};
            $('money-balance').innerHTML =
                '<div class="row text-center">' +
                '<div class="col"><div class="fs-5">' + (b.paid || 0) + ' ₽</div><div class="small text-body-secondary">оплачено</div></div>' +
                '<div class="col"><div class="fs-5">' + (b.spent || 0) + ' ₽</div><div class="small text-body-secondary">израсходовано</div></div>' +
                '<div class="col"><div class="fs-5">' + (b.awaiting || 0) + ' ₽</div><div class="small text-body-secondary">к оплате</div></div>' +
                '<div class="col"><div class="fs-5">' + (s.planned_total || 0) + ' ₽</div><div class="small text-body-secondary">запланировано</div></div>' +
                '</div>' +
                (b.needs_topup ? '<div class="alert alert-warning py-1 mt-2 mb-0 small">Пора пополнить баланс — переведите по реквизитам ниже, владелец подтвердит оплату.</div>' : '');
            var data = await api('/payments');
            $('money-requisites').innerHTML = (data.requisites || []).map(function (r) {
                return '<div class="small">' + esc(r.bank) + ': <a href="' + esc(r.phone_url) + '">' + esc(r.phone) + '</a> — ' + esc(r.holder) + '</div>';
            }).join('');
            $('money-list').innerHTML = (data.payments || []).map(function (p) {
                return '<div class="small">' + esc(fmtDate(p.paid_at)) + ' · ' + p.amount + ' ₽ · ' +
                    (p.status === 'paid' ? '<span class="text-success">получено</span>' : '<span class="text-warning">ожидается</span>') +
                    '</div>';
            }).join('') || '<div class="text-body-secondary small">Оплат пока нет.</div>';
        } catch (e) { alert(e.message); }
    }

    // ---------------- Чат ----------------

    function renderChatMessages(messages, append) {
        var box = $('chat-box');
        if (!append) box.innerHTML = '';
        messages.forEach(function (m) {
            var div = document.createElement('div');
            div.className = 'chat-msg ' + (m.sender === 'client' ? 'chat-mine' : 'chat-theirs');
            div.innerHTML = '<div class="cab-text small">' + esc(m.body) + '</div>' +
                '<div class="text-body-secondary" style="font-size:.7rem">' + esc(fmtDate(m.created_at)) + '</div>';
            box.appendChild(div);
            if (m.id > state.chatLastId) state.chatLastId = m.id;
        });
        if (messages.length) box.scrollTop = box.scrollHeight;
    }

    async function loadChat(reset) {
        try {
            var data = await api('/chat' + (reset ? '' : '?after_id=' + state.chatLastId));
            if (reset) state.chatLastId = 0;
            renderChatMessages(data.messages || [], !reset);
            $('chat-badge').classList.add('d-none');
        } catch (e) { /* сеть мигнула — следующий tick */ }
    }

    function startChatPolling() {
        stopChatPolling();
        state.chatTimer = setInterval(function () { loadChat(false); }, 4000);
    }

    function stopChatPolling() {
        if (state.chatTimer) { clearInterval(state.chatTimer); state.chatTimer = null; }
    }

    document.addEventListener('visibilitychange', function () {
        if (document.hidden) stopChatPolling();
    });

    $('chat-send').addEventListener('click', sendChat);
    $('chat-input').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') sendChat();
    });

    async function sendChat() {
        var body = $('chat-input').value.trim();
        if (!body) return;
        try {
            await api('/chat', { method: 'POST', body: JSON.stringify({ body: body }) });
            $('chat-input').value = '';
            loadChat(false);
        } catch (e) { alert(e.message); }
    }

    boot();
})();
