/* Радар (Ф0.4): лента / архив / источники. Чистый fetch, без зависимостей. */
(function () {
    'use strict';

    const state = {
        feedCursor: null,
        feedLoaded: false,
        savedCursor: null,
        savedLoaded: false,
        lastSeen: null,
        maxSeenCandidate: null,
        savedItemIds: new Set(),
    };

    const $ = (id) => document.getElementById(id);

    const esc = (s) => (s || '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));

    async function api(path, options) {
        const r = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options));
        if (r.status === 401) { location.href = '/login?next=/radar'; throw new Error('401'); }
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || ('HTTP ' + r.status));
        return data;
    }

    const fmtDate = (iso) => {
        if (!iso) return '';
        const d = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
        return d.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    };

    const fmtBytes = (n) => {
        if (n == null) return '';
        if (n >= 1048576) return (n / 1048576).toFixed(1) + ' МБ';
        if (n >= 1024) return Math.round(n / 1024) + ' КБ';
        return n + ' Б';
    };

    function mediaHtml(media, savedId) {
        return (media || []).map((m) => {
            if (m.type === 'photo') {
                const src = (savedId && m.file)
                    ? `/api/radar/saved/${savedId}/media/${encodeURIComponent(m.file)}`
                    : m.url;
                return src ? `<img class="radar-photo mb-2" loading="lazy" src="${esc(src)}">` : '';
            }
            if (m.type === 'video' && m.url) {
                return `<a class="d-block mb-2" href="${esc(m.url)}" target="_blank" rel="noopener">
                          <i class="bi bi-play-circle"></i> Видео</a>`;
            }
            return '';
        }).join('');
    }

    function itemCard(item, opts) {
        const isNew = !opts.saved && state.lastSeen != null && item.id > state.lastSeen;
        const head = esc(item.source ? (item.source.title || item.source.type) : (item.source_title || ''));
        const when = fmtDate(item.published_at || item.saved_at);
        let actions;
        if (opts.saved) {
            actions = `<button class="btn btn-sm btn-outline-danger" data-del-saved="${item.id}" title="Удалить из архива">
                         <i class="bi bi-trash"></i></button>`;
        } else if (state.savedItemIds.has(item.id)) {
            actions = `<span class="text-success" title="В архиве"><i class="bi bi-bookmark-check-fill"></i></span>`;
        } else {
            actions = `<button class="btn btn-sm btn-outline-primary" data-save="${item.id}" title="В архив">
                         <i class="bi bi-bookmark-plus"></i></button>`;
        }
        return `
        <div class="card mb-3 radar-card ${isNew ? 'radar-new' : ''}">
          <div class="card-body">
            <div class="d-flex justify-content-between align-items-start mb-2">
              <div class="small text-secondary">
                <strong>${head}</strong> · ${when}
                ${isNew ? '<span class="badge bg-primary ms-1">новое</span>' : ''}
              </div>
              <div class="d-flex gap-2">${actions}</div>
            </div>
            ${item.title ? `<h3 class="h6">${esc(item.title)}</h3>` : ''}
            ${mediaHtml(item.media, opts.saved ? item.id : null)}
            ${item.text ? `<div class="radar-text">${esc(item.text)}</div>` : ''}
            ${item.url ? `<a class="small d-inline-block mt-2" href="${esc(item.url)}" target="_blank" rel="noopener">оригинал <i class="bi bi-box-arrow-up-right"></i></a>` : ''}
          </div>
        </div>`;
    }

    // ───────────── Лента ─────────────

    async function loadFeed(more) {
        const params = new URLSearchParams({ limit: '30' });
        if (more && state.feedCursor) params.set('before_id', state.feedCursor);
        const data = await api('/api/radar/feed?' + params);
        if (state.lastSeen == null) state.lastSeen = data.last_seen_item_id;
        const html = data.items.map((i) => itemCard(i, { saved: false })).join('');
        if (more) $('feed-list').insertAdjacentHTML('beforeend', html);
        else $('feed-list').innerHTML = html;
        state.feedCursor = data.next_before_id;
        $('feed-more').classList.toggle('d-none', !data.next_before_id);
        $('feed-empty').classList.toggle('d-none', $('feed-list').children.length > 0);

        if (!more && data.items.length) {
            const newCount = data.items.filter((i) => state.lastSeen != null && i.id > state.lastSeen).length;
            const badge = $('new-badge');
            badge.textContent = newCount;
            badge.classList.toggle('d-none', newCount === 0);
            // Сдвигаем курсор: всё показанное считается увиденным.
            state.maxSeenCandidate = data.items[0].id;
            api('/api/radar/feed/seen', {
                method: 'POST', body: JSON.stringify({ item_id: state.maxSeenCandidate }),
            }).catch(() => {});
        }
        state.feedLoaded = true;
    }

    // ───────────── Архив ─────────────

    async function loadSaved(more) {
        const params = new URLSearchParams({ limit: '30' });
        if (more && state.savedCursor) params.set('before_id', state.savedCursor);
        const data = await api('/api/radar/saved?' + params);
        const html = data.items.map((i) => itemCard(i, { saved: true })).join('');
        if (more) $('saved-list').insertAdjacentHTML('beforeend', html);
        else $('saved-list').innerHTML = html;
        state.savedCursor = data.next_before_id;
        $('saved-more').classList.toggle('d-none', !data.next_before_id);
        $('saved-empty').classList.toggle('d-none', $('saved-list').children.length > 0);
        if (data.quota_bytes != null) {
            let line = `Занято ${fmtBytes(data.used_bytes)} из ${fmtBytes(data.quota_bytes)}`;
            if (data.archive && data.archive.writable === false) {
                line += ' · архив заполнен — новые фото сохраняются ссылкой';
            }
            $('quota-line').textContent = line;
        }
        data.items.forEach((i) => { if (i.item_id) state.savedItemIds.add(i.item_id); });
        state.savedLoaded = true;
    }

    // ───────────── Источники ─────────────

    function sourceRow(sub) {
        const s = sub.source || {};
        const icon = { vk: 'bi-chat-square-text', tg: 'bi-telegram' }[s.type] || 'bi-rss';
        const fail = s.fail_count > 0
            ? `<span class="badge bg-warning text-dark" title="${esc(s.last_error || '')}">ошибки: ${s.fail_count}</span>` : '';
        const paused = sub.is_active === false;
        const title = paused
            ? `<span class="text-secondary text-decoration-line-through">${esc(s.title || s.key)}</span>`
            : (s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.key)}</a>`
                     : esc(s.title || s.key));
        const pauseBtn = `<button class="btn btn-sm ${paused ? 'btn-outline-success' : 'btn-outline-secondary'}"
              data-toggle-sub="${sub.id}" data-active="${paused ? '1' : '0'}"
              title="${paused ? 'Возобновить' : 'Поставить на паузу'}">
              <i class="bi ${paused ? 'bi-play-fill' : 'bi-pause-fill'}"></i></button>`;
        return `
        <div class="card mb-2 ${paused ? 'opacity-75' : ''}">
          <div class="card-body d-flex justify-content-between align-items-center py-2">
            <div>
              <i class="bi ${icon} me-1"></i>
              ${title}
              <span class="text-secondary small ms-2">${s.type}</span>
              ${paused ? '<span class="badge bg-secondary ms-1">пауза</span>' : ''} ${fail}
            </div>
            <div class="d-flex gap-2">
              ${pauseBtn}
              <button class="btn btn-sm btn-outline-danger" data-unsub="${sub.id}" title="Отписаться">
                <i class="bi bi-x-lg"></i>
              </button>
            </div>
          </div>
        </div>`;
    }

    // ───────────── Выводы ─────────────

    const OUTPUT_META = {
        feed: { icon: 'bi-broadcast', label: 'Лента радара' },
        telegram: { icon: 'bi-telegram', label: 'Telegram' },
        vk: { icon: 'bi-chat-square-text', label: 'VK' },
    };

    function outputRow(o) {
        const meta = OUTPUT_META[o.type] || { icon: 'bi-send', label: o.type };
        const modeLabel = o.mode === 'full' ? 'целиком' : 'начало+ссылка';
        const off = o.is_active === false;
        const target = o.type === 'feed' ? '' : `<span class="text-secondary small ms-1">${esc(o.target || '')}</span>`;
        const health = o.fail_count > 0
            ? `<span class="badge bg-warning text-dark ms-1" title="${esc(o.last_error || '')}">ошибки: ${o.fail_count}</span>`
            : '';
        const testBtn = o.type === 'feed' ? '' :
            `<button class="btn btn-sm btn-outline-info" data-test-output="${o.id}" title="Тест доставки">
               <i class="bi bi-send-check"></i></button>`;
        return `
        <div class="card mb-2 ${off ? 'opacity-75' : ''}">
          <div class="card-body d-flex justify-content-between align-items-center py-2">
            <div>
              <i class="bi ${meta.icon} me-1"></i>
              <strong>${esc(o.title || meta.label)}</strong>${target}
              <span class="badge bg-light text-dark border ms-1">${modeLabel}</span>
              ${off ? '<span class="badge bg-secondary ms-1">выкл</span>' : ''} ${health}
            </div>
            <div class="d-flex gap-2">
              ${testBtn}
              <button class="btn btn-sm ${off ? 'btn-outline-success' : 'btn-outline-secondary'}"
                  data-toggle-output="${o.id}" data-active="${off ? '1' : '0'}"
                  title="${off ? 'Включить' : 'Выключить'}">
                <i class="bi ${off ? 'bi-play-fill' : 'bi-pause-fill'}"></i></button>
              <button class="btn btn-sm btn-outline-danger" data-del-output="${o.id}" title="Удалить">
                <i class="bi bi-x-lg"></i></button>
            </div>
          </div>
        </div>`;
    }

    async function loadOutputs() {
        const data = await api('/api/radar/outputs');
        $('outputs-list').innerHTML = data.outputs.length
            ? data.outputs.map(outputRow).join('')
            : '<p class="text-secondary text-center py-4">Каналов вывода пока нет. Лента радара работает по умолчанию.</p>';
    }

    async function loadSources() {
        const data = await api('/api/radar/subscriptions');
        $('sources-list').innerHTML = data.subscriptions.length
            ? data.subscriptions.map(sourceRow).join('')
            : '<p class="text-secondary text-center py-4">Источников пока нет.</p>';
    }

    // ───────────── События ─────────────

    document.addEventListener('click', async (e) => {
        const tab = e.target.closest('[data-tab]');
        if (tab) {
            document.querySelectorAll('#radar-tabs .nav-link').forEach((n) => n.classList.remove('active'));
            tab.classList.add('active');
            const name = tab.dataset.tab;
            ['feed', 'saved', 'sources', 'outputs'].forEach((t) =>
                $('tab-' + t).classList.toggle('d-none', t !== name));
            if (name === 'saved' && !state.savedLoaded) loadSaved(false).catch(console.error);
            if (name === 'sources') loadSources().catch(console.error);
            if (name === 'outputs') loadOutputs().catch(console.error);
            return;
        }
        const saveBtn = e.target.closest('[data-save]');
        if (saveBtn) {
            saveBtn.disabled = true;
            try {
                await api('/api/radar/saved', {
                    method: 'POST', body: JSON.stringify({ item_id: Number(saveBtn.dataset.save) }),
                });
                state.savedItemIds.add(Number(saveBtn.dataset.save));
                state.savedLoaded = false;
                saveBtn.outerHTML = '<span class="text-success"><i class="bi bi-bookmark-check-fill"></i></span>';
            } catch (err) { saveBtn.disabled = false; alert('Не удалось сохранить: ' + err.message); }
            return;
        }
        const delBtn = e.target.closest('[data-del-saved]');
        if (delBtn) {
            if (!confirm('Удалить из архива?')) return;
            try {
                await api('/api/radar/saved/' + delBtn.dataset.delSaved, { method: 'DELETE' });
                delBtn.closest('.card').remove();
                loadSaved(false).catch(() => {});
            } catch (err) { alert('Не удалось удалить: ' + err.message); }
            return;
        }
        const unsubBtn = e.target.closest('[data-unsub]');
        if (unsubBtn) {
            if (!confirm('Отписаться от источника?')) return;
            try {
                await api('/api/radar/subscriptions/' + unsubBtn.dataset.unsub, { method: 'DELETE' });
                unsubBtn.closest('.card').remove();
            } catch (err) { alert('Не удалось отписаться: ' + err.message); }
            return;
        }
        const toggleSub = e.target.closest('[data-toggle-sub]');
        if (toggleSub) {
            toggleSub.disabled = true;
            try {
                await api('/api/radar/subscriptions/' + toggleSub.dataset.toggleSub, {
                    method: 'PATCH', body: JSON.stringify({ is_active: toggleSub.dataset.active === '1' }),
                });
                await loadSources();
            } catch (err) { toggleSub.disabled = false; alert('Не удалось: ' + err.message); }
            return;
        }
        const toggleOut = e.target.closest('[data-toggle-output]');
        if (toggleOut) {
            toggleOut.disabled = true;
            try {
                await api('/api/radar/outputs/' + toggleOut.dataset.toggleOutput, {
                    method: 'PATCH', body: JSON.stringify({ is_active: toggleOut.dataset.active === '1' }),
                });
                await loadOutputs();
            } catch (err) { toggleOut.disabled = false; alert('Не удалось: ' + err.message); }
            return;
        }
        const delOut = e.target.closest('[data-del-output]');
        if (delOut) {
            if (!confirm('Удалить канал вывода?')) return;
            try {
                await api('/api/radar/outputs/' + delOut.dataset.delOutput, { method: 'DELETE' });
                delOut.closest('.card').remove();
            } catch (err) { alert('Не удалось удалить: ' + err.message); }
            return;
        }
        const testOut = e.target.closest('[data-test-output]');
        if (testOut) {
            testOut.disabled = true;
            const icon = testOut.querySelector('i');
            const prev = icon ? icon.className : '';
            if (icon) icon.className = 'bi bi-hourglass-split';
            try {
                const res = await api('/api/radar/outputs/' + testOut.dataset.testOutput + '/test', { method: 'POST' });
                alert(res.detail || (res.ok ? 'Отправлено' : 'Не удалось'));
            } catch (err) { alert('Ошибка теста: ' + err.message); }
            finally { testOut.disabled = false; if (icon) icon.className = prev; }
            return;
        }
    });

    $('feed-more').addEventListener('click', () => loadFeed(true).catch(console.error));
    $('saved-more').addEventListener('click', () => loadSaved(true).catch(console.error));

    $('source-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const errBox = $('source-error');
        errBox.classList.add('d-none');
        const btn = $('source-add-btn');
        btn.disabled = true;
        try {
            await api('/api/radar/subscriptions', {
                method: 'POST',
                body: JSON.stringify({ type: $('source-type').value, value: $('source-value').value.trim() }),
            });
            $('source-value').value = '';
            await loadSources();
        } catch (err) {
            errBox.textContent = err.message;
            errBox.classList.remove('d-none');
        } finally { btn.disabled = false; }
    });

    // ───────────── Подключение аккаунтов (Радиоточка) ─────────────

    let tgPoll = null;

    function tgModalState(name) {
        ['loading', 'ready', 'done'].forEach((s) =>
            $('tg-connect-' + s).classList.toggle('d-none', s !== name));
    }

    async function countTelegramOutputs() {
        const data = await api('/api/radar/outputs');
        return data.outputs.filter((o) => o.type === 'telegram').length;
    }

    async function connectTelegram() {
        const modalEl = $('tg-connect-modal');
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        $('tg-connect-error').classList.add('d-none');
        tgModalState('loading');
        modal.show();
        let before = 0;
        try {
            before = await countTelegramOutputs();
            const res = await api('/api/radar/link/telegram', { method: 'POST' });
            $('tg-code').value = res.code;
            if (res.deep_link) {
                $('tg-deep-link').href = res.deep_link;
                $('tg-deep-link').classList.remove('disabled');
            } else {
                $('tg-deep-link').classList.add('disabled');
            }
            $('tg-bot-name').textContent = res.bot_username ? '@' + res.bot_username : 'нашего бота';
            tgModalState('ready');
        } catch (err) {
            $('tg-connect-error').textContent = err.message;
            $('tg-connect-error').classList.remove('d-none');
            tgModalState('ready');
            return;
        }
        // Поллим появление нового telegram-вывода (пользователь нажал Start у бота).
        if (tgPoll) clearInterval(tgPoll);
        tgPoll = setInterval(async () => {
            try {
                const now = await countTelegramOutputs();
                if (now > before) {
                    clearInterval(tgPoll); tgPoll = null;
                    tgModalState('done');
                    await loadOutputs();
                    setTimeout(() => bootstrap.Modal.getOrCreateInstance(modalEl).hide(), 2200);
                }
            } catch (e) { /* сеть моргнула — продолжаем поллить */ }
        }, 3000);
        modalEl.addEventListener('hidden.bs.modal', () => {
            if (tgPoll) { clearInterval(tgPoll); tgPoll = null; }
        }, { once: true });
    }

    document.addEventListener('click', (e) => {
        const connect = e.target.closest('[data-connect]');
        if (!connect || connect.disabled) return;
        if (connect.dataset.connect === 'telegram') connectTelegram().catch(console.error);
    });

    document.getElementById('tg-code-copy')?.addEventListener('click', () => {
        const code = $('tg-code').value;
        navigator.clipboard?.writeText(code).catch(() => {});
    });

    // ───────────── Web-push (Ф0.5) ─────────────

    function b64ToUint8(base64) {
        const padding = '='.repeat((4 - (base64.length % 4)) % 4);
        const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'));
        return Uint8Array.from(raw, (c) => c.charCodeAt(0));
    }

    async function pushState() {
        const reg = await navigator.serviceWorker.ready;
        return { reg, sub: await reg.pushManager.getSubscription() };
    }

    function renderPushIcon(enabled) {
        $('push-icon').className = enabled ? 'bi bi-bell-fill' : 'bi bi-bell';
        $('push-btn').title = enabled
            ? 'Push включён — нажмите, чтобы отключить'
            : 'Включить push о новых элементах';
    }

    async function initPush() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
        const keyResp = await fetch('/api/radar/push/vapid-public-key');
        if (!keyResp.ok) return; // push не настроен на сервере
        $('push-li').classList.remove('d-none');
        const { sub } = await pushState();
        renderPushIcon(!!sub);
    }

    $('push-btn').addEventListener('click', async () => {
        try {
            const { reg, sub } = await pushState();
            if (sub) {
                await api('/api/radar/push/unsubscribe', {
                    method: 'POST', body: JSON.stringify({ endpoint: sub.endpoint }),
                });
                await sub.unsubscribe();
                renderPushIcon(false);
                return;
            }
            if ((await Notification.requestPermission()) !== 'granted') return;
            const { key } = await api('/api/radar/push/vapid-public-key');
            const newSub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: b64ToUint8(key),
            });
            const json = newSub.toJSON();
            await api('/api/radar/push/subscriptions', {
                method: 'POST',
                body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys }),
            });
            renderPushIcon(true);
        } catch (err) { alert('Не удалось переключить push: ' + err.message); }
    });

    initPush().catch(() => {});

    $('logout-btn').addEventListener('click', async () => {
        await fetch('/api/auth/logout', { method: 'POST' });
        location.href = '/login';
    });

    // ───────────── Старт ─────────────

    fetch('/api/auth/me').then((r) => (r.ok ? r.json() : null)).then((u) => {
        if (u) $('me-badge').textContent = u.login;
    }).catch(() => {});
    loadFeed(false).catch(console.error);
})();
