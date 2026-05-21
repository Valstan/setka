// Notifications page specific JavaScript

document.addEventListener('DOMContentLoaded', async () => {
    await loadNotifications();
    await loadActivityWidget();
    await loadHotPosts();
});

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
    } else {
        empty.style.display = 'none';
        
        let html = '<div class="list-group list-group-flush">';
        
        suggestedPosts.forEach(notif => {
            html += `
                <a href="${notif.url}" target="_blank" 
                   class="list-group-item list-group-item-action list-group-item-warning">
                    <div class="d-flex justify-content-between align-items-start">
                        <div class="flex-grow-1">
                            <div class="d-flex align-items-center mb-2">
                                <i class="bi bi-geo-alt-fill text-warning me-2"></i>
                                <h6 class="mb-0">${notif.region_name}</h6>
                            </div>
                            <div class="d-flex align-items-center">
                                <i class="bi bi-envelope me-2 text-muted"></i>
                                <span class="badge bg-warning">
                                    ${notif.suggested_count} предложенн${notif.suggested_count === 1 ? 'ый' : notif.suggested_count < 5 ? 'ых' : 'ых'} пост${notif.suggested_count === 1 ? '' : notif.suggested_count < 5 ? 'а' : 'ов'}
                                </span>
                            </div>
                            ${notif.checked_at ? `
                                <small class="text-muted d-block mt-1">
                                    <i class="bi bi-clock"></i>
                                    Проверено: ${new Date(notif.checked_at).toLocaleTimeString('ru-RU')}
                                </small>
                            ` : ''}
                        </div>
                        <div class="text-end">
                            <i class="bi bi-box-arrow-up-right text-warning fs-4"></i>
                            <small class="d-block text-muted mt-1">Открыть в VK</small>
                        </div>
                    </div>
                </a>
            `;
        });
        
        html += '</div>';
        list.innerHTML = html;
        list.style.display = 'block';
    }
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

    let html = '<div class="list-group list-group-flush">';

    unreadMessages.forEach(notif => {
        html += `
            <a href="${notif.url}" target="_blank"
               class="list-group-item list-group-item-action list-group-item-info">
                <div class="d-flex justify-content-between align-items-start">
                    <div class="flex-grow-1">
                        <div class="d-flex align-items-center mb-2">
                            <i class="bi bi-geo-alt-fill text-info me-2"></i>
                            <h6 class="mb-0">${notif.region_name}</h6>
                        </div>
                        <div class="d-flex align-items-center">
                            <i class="bi bi-chat-dots me-2 text-muted"></i>
                            <span class="badge bg-info">
                                ${notif.unread_count} непрочитанн${notif.unread_count === 1 ? 'ое' : notif.unread_count < 5 ? 'ых' : 'ых'} сообщени${notif.unread_count === 1 ? 'е' : notif.unread_count < 5 ? 'я' : 'й'}
                            </span>
                        </div>
                        ${notif.checked_at ? `
                            <small class="text-muted d-block mt-1">
                                <i class="bi bi-clock"></i>
                                Проверено: ${new Date(notif.checked_at).toLocaleTimeString('ru-RU')}
                            </small>
                        ` : ''}
                    </div>
                    <div class="text-end">
                        <i class="bi bi-box-arrow-up-right text-info fs-4"></i>
                        <small class="d-block text-muted mt-1">Открыть в VK</small>
                    </div>
                </div>
            </a>
        `;
    });

    html += '</div>';
    list.innerHTML = html;
    list.style.display = 'block';
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
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
    } else {
        empty.style.display = 'none';

        let html = '<div class="list-group list-group-flush">';

        visible.forEach(notif => {
            const text = escapeHtml(notif.text || '');
            const postUrl = notif.post_url || '#';
            const communityName = escapeHtml(notif.community_name || notif.region_name || 'Сообщество');
            const cid = notif.comment_id;
            const ownerId = notif.vk_owner_id;
            const postId = notif.vk_post_id;
            const replyBadge = notif.is_reply
                ? '<span class="badge bg-info-subtle text-info-emphasis ms-2">ответ</span>' : '';
            const likesBadge = (notif.likes_count || 0) > 0
                ? `<span class="badge bg-light text-dark ms-1"><i class="bi bi-heart-fill text-danger"></i> ${notif.likes_count}</span>` : '';

            html += `
                <div class="list-group-item list-group-item-light" data-comment-id="${cid}">
                    <div class="d-flex justify-content-between align-items-start">
                        <div class="flex-grow-1">
                            <div class="d-flex align-items-center mb-2">
                                <i class="bi bi-chat-left-text me-2 text-secondary"></i>
                                <h6 class="mb-0">${communityName}</h6>
                                ${replyBadge}${likesBadge}
                            </div>
                            <div class="text-body">
                                <div class="small" style="white-space: pre-wrap;">${text}</div>
                            </div>
                            ${(notif.commented_at || notif.checked_at) ? `
                                <small class="text-muted d-block mt-2">
                                    <i class="bi bi-clock"></i>
                                    ${notif.commented_at ? `Комментарий: ${new Date(notif.commented_at).toLocaleString('ru-RU')}` : `Проверено: ${new Date(notif.checked_at).toLocaleString('ru-RU')}`}
                                </small>
                            ` : ''}
                        </div>
                        <div class="text-end ms-2 d-flex flex-column gap-1">
                            <a href="${postUrl}" target="_blank" class="btn btn-sm btn-outline-secondary" title="Открыть в VK">
                                <i class="bi bi-box-arrow-up-right"></i>
                            </a>
                            <button class="btn btn-sm btn-outline-danger" title="Лайкнуть от имени сообщества"
                                    onclick="likeComment(${ownerId}, ${postId}, ${cid}, this)">
                                <i class="bi bi-heart"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-success" title="Отметить обработанным"
                                    onclick="markHandled('recent_comment', '${cid}', this)">
                                <i class="bi bi-check2"></i>
                            </button>
                        </div>
                    </div>
                </div>
            `;
        });

        html += '</div>';
        list.innerHTML = html;
        list.style.display = 'block';
    }
}

async function likeComment(ownerId, postId, commentId, btn) {
    btn.disabled = true;
    const originalHtml = btn.innerHTML;
    try {
        const resp = await fetch('/api/notifications/comments/like', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ owner_id: ownerId, post_id: postId, comment_id: commentId }),
        });
        const data = await resp.json();
        if (data.success) {
            btn.innerHTML = '<i class="bi bi-heart-fill text-danger"></i>';
            btn.classList.remove('btn-outline-danger');
            btn.classList.add('btn-danger');
        } else {
            btn.innerHTML = originalHtml;
            btn.disabled = false;
            alert(`Не удалось лайкнуть: ${data.error || '?'}`);
        }
    } catch (e) {
        btn.innerHTML = originalHtml;
        btn.disabled = false;
        alert(`Ошибка: ${e.message}`);
    }
}

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
            // Fade out the parent list-group-item and remove
            const card = btn.closest('.list-group-item');
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

