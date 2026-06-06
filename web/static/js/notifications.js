// Notifications page specific JavaScript

document.addEventListener('DOMContentLoaded', async () => {
    await loadNotifications();
    await loadCommunityDmInbox();
    await loadActivityWidget();
    await loadHotPosts();
    // Deep-link from Telegram inline buttons: #section=messages|comments|suggested
    scrollToHashSection();
});

function scrollToHashSection() {
    const hash = (window.location.hash || '').replace(/^#/, '');
    if (!hash) return;
    const m = hash.match(/section=(\w+)/);
    const section = m ? m[1] : null;
    if (!section) return;
    const el = document.getElementById(`section-${section}`);
    if (!el) return;
    // Slight delay so async content rendering completes before scrollIntoView.
    setTimeout(() => {
        el.scrollIntoView({behavior: 'smooth', block: 'start'});
        el.classList.add('border', 'border-3', 'border-primary', 'rounded');
        setTimeout(() => el.classList.remove('border', 'border-3', 'border-primary', 'rounded'), 2500);
    }, 400);
}

async function loadNotifications() {
    try {
        const response = await fetch('/api/notifications/');

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        // Update stats
        updateStats(data);

        // Update timestamp
        updateTimestamp(data.timestamp);

        // Load suggested posts
        loadSuggestedPosts(data.suggested_posts || []);

        // Load unread messages
        loadUnreadMessages(
            data.unread_messages || [],
            data.unread_messages_denied || [],
        );

        // Load recent comments
        loadRecentComments(data.recent_comments || []);

    } catch (error) {
        console.error('Error loading notifications:', error);
        showError(error.message);
    }
}

let _activityChart = null;

async function loadActivityWidget() {
    try {
        const [statsResp, historyResp] = await Promise.all([
            fetch('/api/notifications/stats'),
            fetch('/api/notifications/history'),
        ]);
        if (!statsResp.ok || !historyResp.ok) return;
        const stats = await statsResp.json();
        const history = await historyResp.json();

        // Per-type counters
        const sp = stats.types?.suggested_posts || {};
        const msg = stats.types?.unread_messages || {};
        const cmt = stats.types?.recent_comments || {};
        document.getElementById('stats-sp-runs').textContent = sp.total_runs ?? 0;
        document.getElementById('stats-sp-hits').textContent = sp.with_results_runs ?? 0;
        document.getElementById('stats-msg-runs').textContent = msg.total_runs ?? 0;
        document.getElementById('stats-msg-hits').textContent = msg.with_results_runs ?? 0;
        document.getElementById('stats-cmt-runs').textContent = cmt.total_runs ?? 0;
        document.getElementById('stats-cmt-hits').textContent = cmt.with_results_runs ?? 0;

        const lastRun =
            [sp.last_run_ts, msg.last_run_ts, cmt.last_run_ts]
                .filter(Boolean)
                .sort()
                .pop() || null;
        document.getElementById('activity-summary').textContent = lastRun
            ? `последний прогон: ${new Date(lastRun).toLocaleString('ru-RU')}`
            : 'нет данных';

        renderActivityChart(history);
    } catch (e) {
        console.warn('Activity widget failed:', e);
    }
}

function renderActivityChart(history) {
    const ctx = document.getElementById('activity-chart');
    if (!ctx || typeof Chart === 'undefined') return;

    // Build a unified X-axis from sorted ts values across all three series.
    const allRuns = [
        ...(history.suggested_posts || []),
        ...(history.unread_messages || []),
        ...(history.recent_comments || []),
    ];
    const uniqLabels = Array.from(new Set(allRuns.map(r => r.ts))).sort();
    const labels = uniqLabels.map(ts => new Date(ts).toLocaleTimeString('ru-RU', {
        hour: '2-digit', minute: '2-digit',
    }));

    const tsIndex = new Map(uniqLabels.map((ts, i) => [ts, i]));
    const seriesValues = (raw) => {
        const out = new Array(uniqLabels.length).fill(null);
        for (const r of (raw || [])) {
            const i = tsIndex.get(r.ts);
            if (i !== undefined) out[i] = r.count || 0;
        }
        return out;
    };

    const datasets = [
        { label: 'Предложки', data: seriesValues(history.suggested_posts), borderColor: '#ffc107', backgroundColor: '#ffc10733', tension: 0.25, spanGaps: true },
        { label: 'Сообщения', data: seriesValues(history.unread_messages), borderColor: '#0d6efd', backgroundColor: '#0d6efd33', tension: 0.25, spanGaps: true },
        { label: 'Комменты', data: seriesValues(history.recent_comments), borderColor: '#198754', backgroundColor: '#19875433', tension: 0.25, spanGaps: true },
    ];

    if (_activityChart) _activityChart.destroy();
    _activityChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
                y: { beginAtZero: true, precision: 0 },
            },
            plugins: { legend: { position: 'bottom' } },
        },
    });
}

function updateStats(data) {
    const suggestedCount = data.suggested_count || 0;
    const messagesCount = data.messages_count || 0;
    const commentsCount = data.comments_count || 0;
    const totalCount = data.total_count || 0;
    
    document.getElementById('suggested-count').textContent = suggestedCount;
    document.getElementById('messages-count').textContent = messagesCount;
    document.getElementById('comments-count').textContent = commentsCount;
    document.getElementById('total-count').textContent = totalCount;
}

function updateTimestamp(timestamp) {
    const timeElement = document.getElementById('last-check-time');
    
    if (timestamp) {
        const date = new Date(timestamp);
        timeElement.textContent = date.toLocaleString('ru-RU', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } else {
        timeElement.textContent = 'Ещё не проверялось';
    }
}

function loadSuggestedPosts(suggestedPosts) {
    const loading = document.getElementById('suggested-loading');
    const empty = document.getElementById('suggested-empty');
    const list = document.getElementById('suggested-list');

    loading.style.display = 'none';

    if (suggestedPosts.length === 0) {
        empty.style.display = 'block';
        list.style.display = 'none';
        return;
    }
    empty.style.display = 'none';

    const cards = suggestedPosts.map(notif => {
        const regionName = escapeHtml(notif.region_name || '');
        const cnt = notif.suggested_count;
        const word = `${cnt} предложенн${cnt === 1 ? 'ый' : cnt < 5 ? 'ых' : 'ых'} пост${cnt === 1 ? '' : cnt < 5 ? 'а' : 'ов'}`;
        // Дата предложения (самый старый пост) — видно, как давно висит предложка.
        // На время проверки падаем только если дата предложки недоступна.
        const oldestTs = notif.oldest_suggested_ts;
        const checked = oldestTs
            ? `<div class="notif-meta" title="Дата самого старого предложенного поста"><i class="bi bi-calendar-event"></i> в предложке с ${fmtSuggestedTs(oldestTs)}${daysAgoLabel(oldestTs)}</div>`
            : (notif.checked_at
                ? `<div class="notif-meta"><i class="bi bi-clock"></i> проверено ${new Date(notif.checked_at).toLocaleTimeString('ru-RU')}</div>`
                : '');
        return `
            <a href="${notif.url}" target="_blank" class="notif-card bg-warning-tint text-decoration-none text-body">
                <div class="d-flex justify-content-between align-items-start gap-2">
                    <div class="flex-grow-1">
                        <h6 class="mb-1"><i class="bi bi-geo-alt-fill text-warning"></i> ${regionName}</h6>
                        <span class="badge bg-warning text-dark">${word}</span>
                        ${checked}
                    </div>
                    <i class="bi bi-box-arrow-up-right text-warning fs-5"></i>
                </div>
            </a>
        `;
    }).join('');
    list.innerHTML = `<div class="notif-grid">${cards}</div>`;
    list.style.display = 'block';
}

function loadUnreadMessages(unreadMessages, deniedGroups) {
    const loading = document.getElementById('messages-loading');
    const empty = document.getElementById('messages-empty');
    const denied = document.getElementById('messages-denied');
    const deniedList = document.getElementById('messages-denied-list');
    const list = document.getElementById('messages-list');

    loading.style.display = 'none';
    deniedGroups = deniedGroups || [];

    // Сначала — баннер про denied. Это видно даже когда у части групп есть unread:
    // понимаем что часть охвачена, часть — нет.
    if (deniedGroups.length > 0 && denied) {
        const names = deniedGroups
            .map(g => escapeHtml(g.region_name || g.region_code || `group ${g.vk_group_id}`))
            .join(', ');
        deniedList.innerHTML = `Затронуто ${deniedGroups.length} групп${
            deniedGroups.length === 1 ? 'а' : deniedGroups.length < 5 ? 'ы' : ''
        }: ${names}`;
        denied.style.display = 'block';
    } else if (denied) {
        denied.style.display = 'none';
    }

    if (unreadMessages.length === 0) {
        // empty показываем только когда И unread пуст, И нет denied —
        // иначе denied-баннер сам несёт корректную диагностику.
        empty.style.display = deniedGroups.length === 0 ? 'block' : 'none';
        list.style.display = 'none';
        return;
    }

    empty.style.display = 'none';

    const cards = unreadMessages.map(notif => {
        const groupId = notif.vk_group_id;
        const regionName = escapeHtml(notif.region_name || '');
        const conversations = notif.conversations || [];
        const cnt = notif.unread_count;
        const word = `${cnt} непрочитанн${cnt === 1 ? 'ое' : cnt < 5 ? 'ых' : 'ых'} сообщени${cnt === 1 ? 'е' : cnt < 5 ? 'я' : 'й'}`;

        let convRowsHtml = '';
        if (conversations.length > 0) {
            const rows = conversations.map(c => {
                const peer = c?.conversation?.peer || {};
                const peerId = peer.id;
                if (!peerId) return '';
                const lastMsg = c?.last_message || {};
                const lastText = escapeHtml((lastMsg.text || '').slice(0, 140));
                const peerLabel = `peer ${peerId}`;
                return `
                    <div class="conv-row d-flex justify-content-between align-items-start gap-2">
                        <div class="flex-grow-1">
                            <div class="notif-meta">${peerLabel}</div>
                            <div class="notif-body-text">${lastText || '<em>(без текста)</em>'}</div>
                        </div>
                        <button class="btn btn-sm btn-outline-primary" title="Ответить"
                                onclick='event.stopPropagation(); openReplyModal({kind:"message", groupId:${groupId}, peerId:${peerId}, peerName:${JSON.stringify(peerLabel)}, text:${JSON.stringify(lastMsg.text || "")}, regionName:${JSON.stringify(notif.region_name || "")}})'>
                            <i class="bi bi-reply"></i>
                        </button>
                    </div>
                `;
            }).filter(Boolean).join('');
            convRowsHtml = `<div class="mt-2">${rows}</div>`;
        }

        const checked = notif.checked_at
            ? `<div class="notif-meta"><i class="bi bi-clock"></i> ${new Date(notif.checked_at).toLocaleTimeString('ru-RU')}</div>`
            : '';

        // Вся плашка кликабельна → переход в раздел сообщений группы VK
        // (как у «Предложенных постов»). Кнопки «Ответить» внутри гасят
        // всплытие (event.stopPropagation), чтобы клик по ним не открывал VK.
        return `
            <div class="notif-card bg-info-tint" style="cursor: pointer;" role="link"
                 title="Открыть сообщения сообщества в VK"
                 onclick="window.open('${notif.url}', '_blank')">
                <div class="d-flex justify-content-between align-items-start gap-2">
                    <div class="flex-grow-1">
                        <h6 class="mb-1"><i class="bi bi-geo-alt-fill text-info"></i> ${regionName}</h6>
                        <span class="badge bg-info text-white">${word}</span>
                        ${checked}
                    </div>
                    <i class="bi bi-box-arrow-up-right text-info fs-5"></i>
                </div>
                ${convRowsHtml}
            </div>
        `;
    }).join('');
    list.innerHTML = `<div class="notif-grid">${cards}</div>`;
    list.style.display = 'block';
}

// ─────────────────────────────────────────────────────────────────
// Community DM inbox (Этап 1 — багфикс потери ЛС). Источник — наш стор
// (ad_requests, route='notifications'), а не живой VK unread-счётчик выше.
// Не-рекламные входящие ЛС держатся тут, пока оператор не пометит «Обработано».
// Ответ в интерфейсе (R4) — это Этап 2 (probe-gated); пока отвечаем в VK по ссылке.
// ─────────────────────────────────────────────────────────────────

async function loadCommunityDmInbox() {
    const loading = document.getElementById('community-dm-loading');
    const empty = document.getElementById('community-dm-empty');
    const list = document.getElementById('community-dm-list');
    const countBadge = document.getElementById('community-dm-count');
    try {
        const resp = await fetch('/api/notifications/community-dm');
        if (loading) loading.style.display = 'none';
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const messages = data.messages || [];
        if (countBadge) {
            countBadge.textContent = messages.length;
            countBadge.style.display = messages.length ? '' : 'none';
        }
        if (messages.length === 0) {
            empty.style.display = 'block';
            list.style.display = 'none';
            return;
        }
        empty.style.display = 'none';
        list.innerHTML = `<div class="notif-grid">${messages.map(renderCommunityDmCard).join('')}</div>`;
        list.style.display = 'block';
    } catch (e) {
        if (loading) loading.style.display = 'none';
        console.warn('Community DM inbox failed:', e);
    }
}

function renderCommunityDmCard(m) {
    const community = escapeHtml(m.community_name || 'Сообщество');
    const author = escapeHtml(m.author_name || (m.author_is_group ? 'Сообщество' : `id${m.peer_id}`));
    const text = escapeHtml(m.text_snapshot || '');
    // R4: ответить в приложении (community-токеном через /messages/reply) — для
    // не-групповых авторов, написавших первыми (VK всегда разрешает ответ).
    const canReplyInApp = !m.author_is_group && m.peer_id && Number(m.peer_id) > 0;
    const replyBtn = canReplyInApp ? `
        <button class="btn btn-sm btn-primary" title="Ответить из приложения (от имени сообщества)"
                onclick='openDmReply(${JSON.stringify(m)})'>
            <i class="bi bi-reply"></i> Ответить
        </button>` : '';
    // R5: нитка переписки прямо тут (тред-вью через тот же эндпоинт, что в кабинете).
    const threadBtn = m.origin === 'inbound_dm' ? `
        <button class="btn btn-sm btn-outline-info" title="Показать переписку"
                onclick="toggleDmThread(${m.id}, this)">
            <i class="bi bi-chat-left-text"></i> Переписка
        </button>` : '';
    // VK-deeplink остаётся как запасной путь (ответить вручную в VK).
    const dialogLink = m.dialog_url ? `
        <a href="${m.dialog_url}" target="_blank" rel="noopener noreferrer"
           class="btn btn-sm btn-outline-secondary" title="Открыть диалог в VK">
            <i class="bi bi-box-arrow-up-right"></i>
        </a>` : '';
    return `
        <div class="notif-card bg-primary-tint" data-ad-request-id="${m.id}">
            <div class="d-flex align-items-center gap-2 mb-1">
                <i class="bi bi-person-circle text-primary"></i>
                <h6 class="mb-0 flex-grow-1">${author}</h6>
                <span class="badge bg-primary-subtle text-primary-emphasis">${community}</span>
            </div>
            <div class="notif-body-text">${text || '<em>(без текста)</em>'}</div>
            <div class="notif-actions">
                ${replyBtn}
                ${threadBtn}
                ${dialogLink}
                <button class="btn btn-sm btn-outline-warning" title="Это реклама → перенести в рекламный кабинет"
                        onclick="moveDmToCabinet(${m.id}, this)">
                    <i class="bi bi-megaphone"></i> Это реклама
                </button>
                <button class="btn btn-sm btn-outline-success ms-auto" title="Отметить обработанным"
                        onclick="markDmHandled(${m.id}, this)">
                    <i class="bi bi-check2"></i> Обработано
                </button>
            </div>
            <div class="mt-2" id="dm-thread-${m.id}" style="display:none;"></div>
        </div>
    `;
}

// R4: открыть модалку ответа для входящего ЛS (community-DM из нашего стора).
// Переиспользует общий reply-modal (kind='message'), но передаёт adRequestId,
// чтобы после отправки пометить НАШУ строку обработанной (handling_status=done).
function openDmReply(m) {
    openReplyModal({
        kind: 'message',
        groupId: m.community_vk_id,
        peerId: m.peer_id,
        peerName: m.author_name || `id${m.peer_id}`,
        text: m.text_snapshot || '',
        regionName: m.community_name || '',
        adRequestId: m.id,
    });
}

// R5: тред переписки прямо в карточке уведомления (тот же эндпоинт, что в кабинете).
async function toggleDmThread(adRequestId, btn) {
    const box = document.getElementById(`dm-thread-${adRequestId}`);
    if (!box) return;
    if (box.style.display !== 'none' && box.dataset.loaded === '1') {
        box.style.display = 'none';
        box.dataset.loaded = '0';
        return;
    }
    box.style.display = '';
    box.innerHTML = '<div class="text-muted small">Загрузка переписки…</div>';
    try {
        const resp = await fetch(`/api/ad-cabinet/requests/${adRequestId}/thread`);
        const data = await resp.json();
        const msgs = data.messages || [];
        if (!msgs.length) {
            const why = data.reason === 'no_token' ? 'нет VK-токена'
                : data.reason === 'error' ? 'ошибка VK'
                : 'переписка пуста или недоступна';
            box.innerHTML = `<div class="text-muted small">История недоступна (${why}).</div>`;
            box.dataset.loaded = '1';
            return;
        }
        box.innerHTML = renderDmThread(msgs);
        box.dataset.loaded = '1';
    } catch (e) {
        box.innerHTML = `<div class="text-danger small">Ошибка загрузки переписки: ${escapeHtml(e.message)}</div>`;
    }
}

function renderDmThread(msgs) {
    const rows = msgs.map(mm => {
        const mine = mm.out;  // true — написали мы (сообщество)
        const who = mine
            ? '<i class="bi bi-building"></i> мы'
            : `<i class="bi bi-person"></i> ${escapeHtml(mm.from_name || 'автор')}`;
        const att = mm.attachments
            ? ` <span class="badge bg-light text-dark border">+${mm.attachments} вложений</span>` : '';
        const date = mm.date
            ? new Date(mm.date * 1000).toLocaleString('ru-RU', {
                day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'})
            : '';
        const align = mine ? 'text-end' : '';
        const bg = mine ? 'bg-primary bg-opacity-10' : 'bg-light';
        return `<div class="${align} mb-1">
            <div class="d-inline-block border rounded p-2 ${bg}" style="max-width:85%;text-align:left;white-space:pre-wrap;">
                <div class="text-muted" style="font-size:.75em;">${who} · ${date}</div>
                ${escapeHtml(mm.text || '')}${att}
            </div>
        </div>`;
    }).join('');
    return `<div class="border rounded p-2 bg-white" style="max-height:280px;overflow:auto;">${rows}</div>`;
}

async function markDmHandled(adRequestId, btn) {
    btn.disabled = true;
    try {
        const resp = await fetch(`/api/ad-cabinet/requests/${adRequestId}/handling`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({handling_status: 'done'}),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        fadeOutDmCard(btn);
    } catch (e) {
        btn.disabled = false;
        showToast(`Не удалось отметить: ${e.message}`);
    }
}

async function moveDmToCabinet(adRequestId, btn) {
    btn.disabled = true;
    try {
        const resp = await fetch(`/api/ad-cabinet/requests/${adRequestId}/route`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({route: 'ad_cabinet'}),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        fadeOutDmCard(btn);
        showToast('Перенесено в рекламный кабинет', 'success', 3000);
    } catch (e) {
        btn.disabled = false;
        showToast(`Не удалось перенести: ${e.message}`);
    }
}

// Fade out the card, then refresh the section counter / empty-state.
function fadeOutDmCard(btn) {
    const card = btn.closest('.notif-card');
    if (!card) return;
    card.style.transition = 'opacity 0.3s';
    card.style.opacity = '0.2';
    setTimeout(() => {
        card.remove();
        const list = document.getElementById('community-dm-list');
        const remaining = list ? list.querySelectorAll('.notif-card').length : 0;
        const badge = document.getElementById('community-dm-count');
        if (badge) {
            badge.textContent = remaining;
            badge.style.display = remaining ? '' : 'none';
        }
        if (remaining === 0) {
            if (list) list.style.display = 'none';
            const empty = document.getElementById('community-dm-empty');
            if (empty) empty.style.display = 'block';
        }
    }, 300);
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Дата предложенного поста (unix-сек VK) → «ДД.ММ ЧЧ:ММ».
function fmtSuggestedTs(ts) {
    if (!ts) return '';
    try {
        return new Date(ts * 1000).toLocaleString('ru-RU', {
            day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
        });
    } catch (e) { return ''; }
}

// «(N дн.)» — сколько дней пост висит в предложке (≥1 дня; иначе пусто).
function daysAgoLabel(ts) {
    if (!ts) return '';
    const days = Math.floor((Date.now() - ts * 1000) / 86400000);
    return days >= 1 ? ` <span class="text-danger">(${days} дн.)</span>` : '';
}

async function loadRecentComments(recentComments) {
    const loading = document.getElementById('comments-loading');
    const empty = document.getElementById('comments-empty');
    const list = document.getElementById('comments-list');

    loading.style.display = 'none';

    // Newest first (defensive: backend should already sort)
    recentComments.sort((a, b) => {
        const ad = a.commented_at || a.checked_at || '';
        const bd = b.commented_at || b.checked_at || '';
        return bd.localeCompare(ad);
    });

    // Fetch handled set so we can hide / dim already-processed items
    let handledIds = new Set();
    try {
        const resp = await fetch('/api/notifications/handled/recent_comment');
        if (resp.ok) {
            const data = await resp.json();
            handledIds = new Set((data.ids || []).map(String));
        }
    } catch (e) { /* non-fatal */ }

    const visible = recentComments.filter(n => !handledIds.has(String(n.comment_id)));

    if (visible.length === 0) {
        empty.style.display = 'block';
        list.style.display = 'none';
        return;
    }
    empty.style.display = 'none';

    const cards = visible.map(notif => {
        const text = escapeHtml(notif.text || '');
        const postUrl = notif.post_url || '#';
        const communityName = escapeHtml(notif.community_name || notif.region_name || 'Сообщество');
        const cid = notif.comment_id;
        const ownerId = notif.vk_owner_id;
        const postId = notif.vk_post_id;
        const replyBadge = notif.is_reply
            ? '<span class="badge bg-info-subtle text-info-emphasis">ответ</span>' : '';
        const likesBadge = (notif.likes_count || 0) > 0
            ? `<span class="badge bg-light text-dark"><i class="bi bi-heart-fill text-danger"></i> ${notif.likes_count}</span>` : '';
        const dateHtml = (notif.commented_at || notif.checked_at) ? `
            <div class="notif-meta"><i class="bi bi-clock"></i>
                ${notif.commented_at ? new Date(notif.commented_at).toLocaleString('ru-RU') : new Date(notif.checked_at).toLocaleString('ru-RU')}
            </div>` : '';
        return `
            <div class="notif-card bg-secondary-tint" data-comment-id="${cid}">
                <div class="d-flex align-items-center gap-2 mb-1">
                    <i class="bi bi-chat-left-text text-secondary"></i>
                    <h6 class="mb-0 flex-grow-1">${communityName}</h6>
                    ${replyBadge}${likesBadge}
                </div>
                <div class="notif-body-text">${text}</div>
                ${dateHtml}
                <div class="notif-actions">
                    <a href="${postUrl}" target="_blank" class="btn btn-sm btn-outline-secondary" title="Открыть в VK">
                        <i class="bi bi-box-arrow-up-right"></i>
                    </a>
                    <button class="btn btn-sm btn-outline-primary" title="Ответить от имени сообщества"
                            onclick='openReplyModal({kind:"comment", ownerId:${ownerId}, postId:${postId}, commentId:${cid}, text:${JSON.stringify(notif.text || "")}, regionName:${JSON.stringify(notif.community_name || notif.region_name || "")}})'>
                        <i class="bi bi-reply"></i>
                    </button>
                    <a href="https://vk.com/wall${ownerId}_${postId}?reply=${cid}&thread=${cid}"
                       target="_blank" rel="noopener noreferrer"
                       class="btn btn-sm btn-outline-danger"
                       title="Открыть пост в VK и поставить лайк руками — VK API не разрешает лайки от имени сообщества (error 27), а user-token со scope wall на физлиц больше не выдаётся.">
                        <i class="bi bi-heart"></i>
                        <i class="bi bi-box-arrow-up-right" style="font-size: 0.7em;"></i>
                    </a>
                    <button class="btn btn-sm btn-outline-success ms-auto" title="Отметить обработанным"
                            onclick="markHandled('recent_comment', '${cid}', this)">
                        <i class="bi bi-check2"></i>
                    </button>
                </div>
            </div>
        `;
    }).join('');
    list.innerHTML = `<div class="notif-grid">${cards}</div>`;
    list.style.display = 'block';
}

// Toast helper — non-blocking notification вместо alert().
// Один-два таких подряд автоматически складываются в стек.
function showToast(message, level = 'danger', durationMs = 6000) {
    const node = document.createElement('div');
    node.className = `notif-toast alert alert-${level} alert-dismissible fade show shadow-sm`;
    node.setAttribute('role', 'alert');
    node.innerHTML = `${message}<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Закрыть"></button>`;
    document.body.appendChild(node);
    setTimeout(() => {
        node.classList.remove('show');
        setTimeout(() => node.remove(), 300);
    }, durationMs);
}

// Старый likeComment() и explainLikeError() удалены — VK 2026 принципиально
// не разрешает likes.add ни через community-token (error 27), ни через user-token
// без scope `wall`, а сам этот scope выпуск для physлиц перестал выдавать
// (legacy Standalone-app форма недоступна, mobile app_id получают IP-pinning).
// Кнопка теперь — обычная ссылка-deeplink в VK с фокусом на комменте, лайк
// ставится руками. Backend endpoint `/api/notifications/comments/like` остаётся
// в коде на случай если когда-нибудь будет работающий user-token с wall scope.

async function markHandled(notificationType, itemId, btn) {
    btn.disabled = true;
    try {
        const resp = await fetch('/api/notifications/handled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notification_type: notificationType, item_id: String(itemId) }),
        });
        const data = await resp.json();
        if (data.success) {
            // Fade out parent — поддерживаем оба обвалёра (старый .list-group-item
            // и новый .notif-card в grid-layout), чтобы не сломать если где-то
            // остался legacy вызов.
            const card = btn.closest('.notif-card') || btn.closest('.list-group-item');
            if (card) {
                card.style.transition = 'opacity 0.3s';
                card.style.opacity = '0.2';
                setTimeout(() => card.remove(), 300);
            }
        } else {
            btn.disabled = false;
            alert('Не удалось отметить');
        }
    } catch (e) {
        btn.disabled = false;
        alert(`Ошибка: ${e.message}`);
    }
}

async function loadHotPosts() {
    try {
        const resp = await fetch('/api/notifications/hot-posts?min_comments=5&limit=5');
        if (!resp.ok) return;
        const data = await resp.json();
        const card = document.getElementById('hot-posts-card');
        const list = document.getElementById('hot-posts-list');
        if (!card || !list) return;
        if (!data.posts || data.posts.length === 0) {
            card.style.display = 'none';
            return;
        }
        card.style.display = 'block';
        list.innerHTML = data.posts.map(p => `
            <a href="${p.post_url}" target="_blank" class="list-group-item list-group-item-action">
                <div class="d-flex justify-content-between align-items-start">
                    <div>
                        <strong>${escapeHtml(p.region_name || '')}</strong>
                        <div class="small text-muted mt-1">${escapeHtml(p.preview || '')}</div>
                    </div>
                    <div class="text-end ms-2">
                        <span class="badge bg-warning text-dark">${p.unhandled_comments} 🟡</span>
                        <small class="d-block text-muted">из ${p.total_comments}</small>
                    </div>
                </div>
            </a>
        `).join('');
    } catch (e) { /* non-fatal */ }
}

// ─────────────────────────────────────────────────────────────────
// Reply modal (etap 4b): shared by comment-reply and message-reply
// ─────────────────────────────────────────────────────────────────

// State of the currently-open modal. Set by openReplyModal(),
// consumed by sendReply() / generateAiDraft() / loadTemplatesIntoSelect().
let _replyCtx = null;

function openReplyModal(ctx) {
    // ctx shape:
    //   {kind:'comment', ownerId, postId, commentId, text, regionName}
    //   {kind:'message', groupId, peerId, peerName, text, regionName}
    _replyCtx = ctx;

    const title = document.getElementById('reply-modal-title');
    const ctxText = document.getElementById('reply-context-text');
    const textarea = document.getElementById('reply-textarea');
    const status = document.getElementById('reply-status');
    const tplSelect = document.getElementById('reply-template-select');
    const aiBtn = document.getElementById('reply-ai-btn');

    textarea.value = '';
    status.textContent = '';
    status.className = 'mt-2 small';

    if (ctx.kind === 'comment') {
        title.textContent = `Ответить на комментарий — ${ctx.regionName || ''}`;
        ctxText.textContent = ctx.text || '(нет текста)';
        // Templates are for messages; AI is for both — show AI, hide templates.
        tplSelect.style.display = 'none';
        aiBtn.style.display = '';
    } else if (ctx.kind === 'message') {
        title.textContent = `Ответ в диалог — ${ctx.regionName || ''}${ctx.peerName ? ' / ' + ctx.peerName : ''}`;
        ctxText.textContent = ctx.text || '(нет последнего сообщения)';
        tplSelect.style.display = '';
        aiBtn.style.display = '';
        loadTemplatesIntoSelect(tplSelect);
    }

    const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('reply-modal'));
    modal.show();
    setTimeout(() => textarea.focus(), 250);
}

async function loadTemplatesIntoSelect(selectEl) {
    selectEl.innerHTML = '<option value="">— Шаблон —</option>';
    try {
        const resp = await fetch('/api/templates/');
        if (!resp.ok) return;
        const data = await resp.json();
        for (const t of (data.templates || [])) {
            const opt = document.createElement('option');
            opt.value = t.body;
            opt.textContent = t.title;
            selectEl.appendChild(opt);
        }
    } catch (e) { /* non-fatal */ }
    selectEl.onchange = () => {
        if (selectEl.value) {
            document.getElementById('reply-textarea').value = selectEl.value;
            selectEl.value = '';
        }
    };
}

async function generateAiDraft() {
    if (!_replyCtx) return;
    const btn = document.getElementById('reply-ai-btn');
    const textarea = document.getElementById('reply-textarea');
    const status = document.getElementById('reply-status');
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Генерация…';
    status.textContent = '';
    try {
        const resp = await fetch('/api/notifications/comments/draft', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                text: _replyCtx.text || '',
                region_name: _replyCtx.regionName || null,
                style: 'friendly',
            }),
        });
        const data = await resp.json();
        if (data.success === false || !data.draft) {
            if (data.prompt) {
                // Groq unavailable (no budget / quota) — clipboard fallback:
                // hand the ready prompt to the operator's own LLM.
                await offerDraftPromptFallback(data.prompt, status);
            } else {
                status.className = 'mt-2 small text-danger';
                status.textContent = `AI не смог сгенерировать: ${data.error || '—'}`;
            }
        } else {
            textarea.value = data.draft;
            status.className = 'mt-2 small text-muted';
            status.textContent = `черновик ${data.model || 'AI'} — отредактируйте перед отправкой`;
        }
    } catch (e) {
        status.className = 'mt-2 small text-danger';
        status.textContent = `Ошибка AI: ${e.message}`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = orig;
    }
}

// Clipboard fallback when the AI server is unavailable: copy the ready
// prompt so the operator pastes it into ChatGPT/Claude, then pastes the
// answer back into the textarea. Keeps the button useful with zero budget.
async function offerDraftPromptFallback(promptText, status) {
    status.className = 'mt-2 small text-muted';
    try {
        await navigator.clipboard.writeText(promptText);
        status.innerHTML =
            '🤖 ИИ-сервер недоступен. Промпт <b>скопирован в буфер</b> — вставьте его ' +
            'в ChatGPT/Claude/любую нейросеть, а готовый ответ вставьте в поле выше ' +
            'и отредактируйте.';
    } catch (e) {
        // Clipboard blocked (non-secure context) — show prompt for manual copy.
        status.innerHTML =
            '🤖 ИИ-сервер недоступен. Скопируйте промпт из окна и вставьте в нейросеть.';
        window.prompt('Скопируйте промпт (Ctrl+C) и вставьте в нейросеть:', promptText);
    }
}

async function sendReply() {
    if (!_replyCtx) return;
    const textarea = document.getElementById('reply-textarea');
    const sendBtn = document.getElementById('reply-send-btn');
    const status = document.getElementById('reply-status');
    const message = (textarea.value || '').trim();
    if (!message) {
        status.className = 'mt-2 small text-danger';
        status.textContent = 'Введите текст ответа';
        return;
    }
    const orig = sendBtn.innerHTML;
    sendBtn.disabled = true;
    sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Отправка…';
    status.textContent = '';

    let url, body, itemId, notifType;
    if (_replyCtx.kind === 'comment') {
        url = '/api/notifications/comments/reply';
        body = {
            owner_id: _replyCtx.ownerId,
            post_id: _replyCtx.postId,
            comment_id: _replyCtx.commentId,
            message,
        };
        itemId = String(_replyCtx.commentId);
        notifType = 'recent_comment';
    } else {
        url = '/api/notifications/messages/reply';
        body = {
            group_id: _replyCtx.groupId,
            peer_id: _replyCtx.peerId,
            message,
        };
        itemId = String(_replyCtx.peerId);
        notifType = 'unread_message';
    }

    try {
        const resp = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.success) {
            status.className = 'mt-2 small text-success';
            status.textContent = `✅ Отправлено (via ${data.via || '?'})`;
            if (_replyCtx.adRequestId) {
                // DB-backed community DM (Этап 2): помечаем НАШУ строку обработанной
                // (handling_status=done) и обновляем ленту ЛС, а не Redis-handled.
                // Новое входящее от этого же человека переоткроет строку (UPSERT в скане).
                fetch(`/api/ad-cabinet/requests/${_replyCtx.adRequestId}/handling`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({handling_status: 'done'}),
                }).catch(() => {});
                setTimeout(() => {
                    bootstrap.Modal.getInstance(document.getElementById('reply-modal'))?.hide();
                    loadCommunityDmInbox();
                }, 900);
            } else {
                // Legacy (комменты / старые VK-unread сообщения): Redis-handled + полный reload.
                fetch('/api/notifications/handled', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({notification_type: notifType, item_id: itemId}),
                }).catch(() => {});
                setTimeout(() => {
                    bootstrap.Modal.getInstance(document.getElementById('reply-modal'))?.hide();
                    loadNotifications();
                }, 900);
            }
        } else {
            status.className = 'mt-2 small text-danger';
            status.textContent = `Не отправилось: [${data.error_code || '?'}] ${data.error || '—'}`;
        }
    } catch (e) {
        status.className = 'mt-2 small text-danger';
        status.textContent = `Ошибка: ${e.message}`;
    } finally {
        sendBtn.disabled = false;
        sendBtn.innerHTML = orig;
    }
}

// ─────────────────────────────────────────────────────────────────

async function checkNotificationsNow() {
    const btn = document.getElementById('check-now-btn');
    const originalText = btn.innerHTML;
    
    try {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Проверка...';
        
        const response = await fetch('/api/notifications/check-now', {
            method: 'POST'
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const result = await response.json();
        
        // Reload notifications
        await loadNotifications();
        
        // Show success message
        const totalCount = result.total_count || 0;
        const suggestedCount = result.suggested_count || 0;
        const messagesCount = result.messages_count || 0;
        const commentsCount = result.comments_count || 0;
        
        if (totalCount > 0) {
            let message = '✅ Проверка завершена!\n\n';
            if (suggestedCount > 0) {
                message += `📝 Предложенных постов: ${suggestedCount}\n`;
            }
            if (messagesCount > 0) {
                message += `💬 Непрочитанных сообщений: ${messagesCount}\n`;
            }
            if (commentsCount > 0) {
                message += `💭 Комментариев за сутки: ${commentsCount}\n`;
            }
            alert(message);
        } else {
            alert('✅ Нет новых уведомлений. Все проверено!');
        }
        
    } catch (error) {
        console.error('Error checking notifications:', error);
        alert('Ошибка при проверке: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

async function clearNotifications() {
    if (!confirm('Вы уверены, что хотите очистить все уведомления?')) {
        return;
    }
    
    const btn = document.getElementById('clear-btn');
    const originalText = btn.innerHTML;
    
    try {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Очистка...';
        
        const response = await fetch('/api/notifications/', {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        // Reload notifications
        await loadNotifications();
        
        alert('✅ Все уведомления очищены!');
        
    } catch (error) {
        console.error('Error clearing notifications:', error);
        alert('Ошибка при очистке: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

function showError(message) {
    const errorAlert = document.getElementById('error-alert');
    const errorText = document.getElementById('error-text');
    
    errorText.textContent = message;
    errorAlert.style.display = 'block';
    
    // Hide loading indicators
    document.getElementById('suggested-loading').style.display = 'none';
    document.getElementById('messages-loading').style.display = 'none';
    document.getElementById('comments-loading').style.display = 'none';
}

