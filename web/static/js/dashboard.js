// Dashboard specific JavaScript

document.addEventListener('DOMContentLoaded', async () => {
    console.log('Dashboard loaded, starting data loading...');
    await loadDashboardData();
    console.log('Dashboard data loading completed');
});

async function loadDashboardData() {
    // Load all dashboard components in parallel
    await Promise.all([
        loadNotifications(),
        loadVKMonitoringStats()
    ]);
}

async function loadNotifications() {
    const loadingElement = document.getElementById('notifications-loading');
    const emptyElement = document.getElementById('notifications-empty');
    const listElement = document.getElementById('notifications-list');
    const errorElement = document.getElementById('notifications-error');
    const timestampElement = document.getElementById('notifications-timestamp');
    
    try {
        console.log('Loading notifications...');
        const response = await apiClient.getNotifications();
        
        // Скрываем спиннер
        loadingElement.style.display = 'none';
        
        // Обновляем timestamp
        if (response.timestamp) {
            const lastCheck = new Date(response.timestamp);
            timestampElement.textContent = `Последняя проверка: ${lastCheck.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;
        } else {
            timestampElement.textContent = 'Данные не обновлялись';
        }
        
        const suggestedPosts = response.suggested_posts || [];
        const unreadMessages = response.unread_messages || [];
        const totalCount = response.total_count || 0;
        
        if (totalCount === 0) {
            // Показываем сообщение об отсутствии уведомлений
            emptyElement.style.display = 'block';
            listElement.style.display = 'none';
            errorElement.style.display = 'none';
        } else {
            // Показываем список уведомлений
            emptyElement.style.display = 'none';
            listElement.style.display = 'block';
            errorElement.style.display = 'none';
            
            let notificationsHtml = '';
            
            // Добавляем предложенные посты
            if (suggestedPosts.length > 0) {
                notificationsHtml += '<div class="mb-3"><h6 class="text-warning"><i class="bi bi-lightbulb"></i> Предложенные посты</h6>';
                suggestedPosts.forEach(notif => {
                    notificationsHtml += `
                        <div class="alert alert-warning alert-sm mb-2">
                            <div class="d-flex justify-content-between align-items-start">
                                <div>
                                    <strong>${notif.region_name}</strong><br>
                                    <small>${notif.suggested_count} пост(ов) ожидает модерации</small>
                                </div>
                                <a href="${notif.url}" target="_blank" class="btn btn-sm btn-outline-warning">
                                    <i class="bi bi-box-arrow-up-right"></i> Открыть
                                </a>
                            </div>
                        </div>
                    `;
                });
                notificationsHtml += '</div>';
            }
            
            // Добавляем непрочитанные сообщения
            if (unreadMessages.length > 0) {
                notificationsHtml += '<div class="mb-3"><h6 class="text-info"><i class="bi bi-chat-dots"></i> Непрочитанные сообщения</h6>';
                unreadMessages.forEach(notif => {
                    notificationsHtml += `
                        <div class="alert alert-info alert-sm mb-2">
                            <div class="d-flex justify-content-between align-items-start">
                                <div>
                                    <strong>${notif.region_name}</strong><br>
                                    <small>${notif.unread_count} непрочитанных сообщений</small>
                                </div>
                                <a href="${notif.url}" target="_blank" class="btn btn-sm btn-outline-info">
                                    <i class="bi bi-box-arrow-up-right"></i> Открыть
                                </a>
                            </div>
                        </div>
                    `;
                });
                notificationsHtml += '</div>';
            }
            
            listElement.innerHTML = notificationsHtml;
        }
        
    } catch (error) {
        console.error('Error loading notifications:', error);
        
        // Скрываем спиннер и показываем ошибку
        loadingElement.style.display = 'none';
        emptyElement.style.display = 'none';
        listElement.style.display = 'none';
        errorElement.style.display = 'block';
        
        const errorText = document.getElementById('notifications-error-text');
        errorText.textContent = `Ошибка загрузки: ${error.message}`;
        timestampElement.textContent = 'Ошибка загрузки';
    }
}

async function loadVKMonitoringStats() {
    try {
        const stats = await apiClient.getVKStats();
        
        // Update basic stats
        document.getElementById('vk-requests-today').textContent = stats.requests_today || 0;
        document.getElementById('vk-requests-per-hour').textContent = stats.requests_per_hour || 0;
        document.getElementById('vk-tokens-active').textContent = stats.active_tokens || 0;
        
        // Update last scan time
        if (stats.last_scan) {
            const lastScan = new Date(stats.last_scan);
            document.getElementById('vk-last-scan').textContent = lastScan.toLocaleTimeString('ru-RU');
        } else {
            document.getElementById('vk-last-scan').textContent = 'Никогда';
        }
        
        // Update scan frequency progress bar
        const frequencyPercent = Math.min((stats.scan_frequency || 0) * 100, 100);
        const frequencyBar = document.getElementById('scan-frequency-bar');
        const frequencyText = document.getElementById('scan-frequency-text');
        frequencyBar.style.width = `${frequencyPercent}%`;
        frequencyText.textContent = `${frequencyPercent.toFixed(1)}%`;
        
        // Update load indicators
        const currentLoad = document.getElementById('current-load');
        const limitUsage = document.getElementById('limit-usage');
        const nextScan = document.getElementById('next-scan');
        
        if (stats.current_load === 'low') {
            currentLoad.textContent = 'Низкая';
            currentLoad.className = 'text-success';
        } else if (stats.current_load === 'medium') {
            currentLoad.textContent = 'Средняя';
            currentLoad.className = 'text-warning';
        } else {
            currentLoad.textContent = 'Высокая';
            currentLoad.className = 'text-danger';
        }
        
        limitUsage.textContent = `${stats.limit_usage || 0}%`;
        nextScan.textContent = stats.next_scan || 'Через 45 мин';
        
        // Update tokens status with edit buttons
        const tokensStatus = document.getElementById('tokens-status');
        if (stats.tokens_status && stats.tokens_status.length > 0) {
            tokensStatus.innerHTML = stats.tokens_status.map(token => `
                <div class="d-flex align-items-center justify-content-between mb-1">
                    <div class="d-flex align-items-center">
                        <span class="badge ${token.active ? 'bg-success' : 'bg-danger'} me-2">${token.name}</span>
                        <small class="text-muted">${token.last_used ? new Date(token.last_used).toLocaleTimeString('ru-RU') : 'Никогда'}</small>
                    </div>
                    <button class="btn btn-sm btn-outline-secondary" onclick="editToken('${token.name}')" title="Изменить токен">
                        <i class="bi bi-pencil"></i>
                    </button>
                </div>
            `).join('');
        } else {
            tokensStatus.innerHTML = '<small class="text-muted">Нет данных о токенах</small>';
        }
        
    } catch (err) {
        console.error('Error loading VK monitoring stats:', err);
        // Set default values on error
        document.getElementById('vk-requests-today').textContent = 'Ошибка';
        document.getElementById('vk-requests-per-hour').textContent = 'Ошибка';
        document.getElementById('vk-tokens-active').textContent = 'Ошибка';
        document.getElementById('vk-last-scan').textContent = 'Ошибка';
        
        // Show error in tokens status
        const tokensStatus = document.getElementById('tokens-status');
        tokensStatus.innerHTML = '<small class="text-danger">Ошибка загрузки статуса токенов</small>';
    }
}

async function refreshVKStats() {
    const btn = document.getElementById('refresh-vk-btn') || document.querySelector('button[onclick="refreshVKStats()"]');
    const originalText = btn.innerHTML;
    
    try {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Обновление...';
        
        await loadVKMonitoringStats();
        showToast('Статистика VK API обновлена', 'success');
        
    } catch (err) {
        showToast('Ошибка при обновлении статистики: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

// Token Management Functions
let currentEditingToken = null;

async function refreshTokens() {
    await loadVKMonitoringStats();
    showToast('Статус токенов обновлен', 'success');
}

async function validateAllTokens() {
    const btn = document.querySelector('button[onclick="validateAllTokens()"]');
    const originalText = btn.innerHTML;
    
    try {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Проверка...';
        
        const response = await fetch('/api/tokens/validate-all', {
            method: 'POST'
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const results = await response.json();
        
        // Обновить статус токенов
        await loadVKMonitoringStats();
        
        // Показать результаты
        const validCount = results.filter(r => r.is_valid).length;
        const totalCount = results.length;
        
        showToast(`Проверка завершена: ${validCount}/${totalCount} токенов валидны`, 'success');
        
    } catch (err) {
        showToast('Ошибка при проверке токенов: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

function editToken(tokenName) {
    currentEditingToken = tokenName;
    
    // Заполнить форму
    document.getElementById('tokenName').value = tokenName;
    document.getElementById('tokenValue').value = '';
    document.getElementById('tokenValidationResult').style.display = 'none';
    
    // Показать модальное окно
    const modal = new bootstrap.Modal(document.getElementById('tokenModal'));
    modal.show();
}

async function saveToken() {
    if (!currentEditingToken) return;
    
    const tokenValue = document.getElementById('tokenValue').value.trim();
    const validateToken = document.getElementById('validateToken').checked;
    const spinner = document.getElementById('saveTokenSpinner');
    const saveBtn = spinner.parentElement;
    
    if (!tokenValue) {
        showToast('Введите токен', 'error');
        return;
    }
    
    if (!tokenValue.startsWith('vk1.a.')) {
        showToast('Токен должен начинаться с "vk1.a."', 'error');
        return;
    }
    
    try {
        saveBtn.disabled = true;
        spinner.style.display = 'inline-block';
        
        const response = await fetch(`/api/tokens/${currentEditingToken}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                token: tokenValue,
                validate_token: validateToken
            })
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `HTTP ${response.status}`);
        }
        
        const result = await response.json();
        
        // Показать результат валидации
        const validationResult = document.getElementById('tokenValidationResult');
        if (validateToken) {
            validationResult.innerHTML = `
                <div class="alert ${result.validation_status === 'valid' ? 'alert-success' : 'alert-danger'}">
                    <h6><i class="bi bi-${result.validation_status === 'valid' ? 'check-circle' : 'exclamation-triangle'}"></i> 
                    ${result.validation_status === 'valid' ? 'Токен валиден' : 'Токен невалиден'}</h6>
                    ${result.error_message ? `<small>${result.error_message}</small>` : ''}
                    ${result.user_info ? `<div class="mt-2"><strong>Пользователь:</strong> ${result.user_info.first_name} ${result.user_info.last_name}</div>` : ''}
                </div>
            `;
            validationResult.style.display = 'block';
        }
        
        // Обновить статус токенов
        await loadVKMonitoringStats();
        
        showToast('Токен успешно обновлен', 'success');
        
        // Закрыть модальное окно через 2 секунды если токен валиден
        if (result.validation_status === 'valid') {
            setTimeout(() => {
                const modal = bootstrap.Modal.getInstance(document.getElementById('tokenModal'));
                modal.hide();
            }, 2000);
        }
        
    } catch (err) {
        showToast('Ошибка при сохранении токена: ' + err.message, 'error');
    } finally {
        saveBtn.disabled = false;
        spinner.style.display = 'none';
    }
}

// Check notifications now function
async function checkSuggestedNow() {
    const btn = document.getElementById('check-suggested-btn');
    const originalText = btn.innerHTML;
    
    try {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Проверка...';
        
        // Показываем спиннер загрузки
        const loadingElement = document.getElementById('notifications-loading');
        const emptyElement = document.getElementById('notifications-empty');
        const listElement = document.getElementById('notifications-list');
        const errorElement = document.getElementById('notifications-error');
        
        loadingElement.style.display = 'block';
        emptyElement.style.display = 'none';
        listElement.style.display = 'none';
        errorElement.style.display = 'none';
        
        // Запускаем проверку
        const result = await apiClient.checkNotificationsNow();
        
        if (result.success) {
            showToast(`Проверка завершена: найдено ${result.total_count} уведомлений`, 'success');
            
            // Перезагружаем уведомления
            await loadNotifications();
        } else {
            throw new Error(result.message || 'Ошибка при проверке');
        }
        
    } catch (err) {
        showToast('Ошибка при проверке уведомлений: ' + err.message, 'error');
        
        // Показываем ошибку
        const loadingElement = document.getElementById('notifications-loading');
        const errorElement = document.getElementById('notifications-error');
        const errorText = document.getElementById('notifications-error-text');
        
        loadingElement.style.display = 'none';
        errorElement.style.display = 'block';
        errorText.textContent = `Ошибка проверки: ${err.message}`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

// Utility function for toast notifications
function showToast(message, type = 'info') {
    // Create toast element
    const toastHtml = `
        <div class="toast align-items-center text-white bg-${type === 'error' ? 'danger' : type === 'success' ? 'success' : 'info'} border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        </div>
    `;
    
    // Add to toast container
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
        toastContainer.style.zIndex = '9999';
        document.body.appendChild(toastContainer);
    }
    
    toastContainer.insertAdjacentHTML('beforeend', toastHtml);
    
    // Show toast
    const toastElement = toastContainer.lastElementChild;
    const toast = new bootstrap.Toast(toastElement);
    toast.show();
    
    // Remove toast element after it's hidden
    toastElement.addEventListener('hidden.bs.toast', () => {
        toastElement.remove();
    });
}