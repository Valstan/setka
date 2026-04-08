// Main JavaScript for SETKA Web UI

// Check system status on page load
document.addEventListener('DOMContentLoaded', async () => {
    await updateSystemStatus();
    
    // Update system status every 30 seconds
    setInterval(updateSystemStatus, 30000);
});

// Update system status indicator in navbar
async function updateSystemStatus() {
    const statusElement = document.getElementById('system-status');
    if (!statusElement) return;
    
    const statusText = statusElement.querySelector('.status-text');
    
    try {
        const health = await apiClient.getHealth();
        
        statusElement.classList.remove('text-success', 'text-warning', 'text-danger');
        if (health.status === 'healthy') {
            statusElement.classList.add('text-success');
            statusText.textContent = 'Система: OK';
            statusElement.title = 'Система работает нормально';
        } else {
            statusElement.classList.add('text-warning');
            statusText.textContent = 'Система: WARN';
            statusElement.title = 'Система работает с предупреждениями';
        }
    } catch (error) {
        statusElement.classList.add('text-danger');
        statusText.textContent = 'Система: OFF';
        statusElement.title = 'Система недоступна: ' + error.message;
        console.error('Failed to check system status:', error);
    }
}

// Utility functions
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('ru-RU', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function formatNumber(num) {
    return new Intl.NumberFormat('ru-RU').format(num);
}

function truncateText(text, maxLength = 100) {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

function showToast(message, type = 'info') {
    // Simple toast notification
    const toast = document.createElement('div');
    toast.className = `alert alert-${type} position-fixed top-0 end-0 m-3`;
    toast.style.zIndex = '9999';
    toast.style.minWidth = '300px';
    toast.innerHTML = `
        <div class="d-flex justify-content-between align-items-center">
            <span>${message}</span>
            <button type="button" class="btn-close" onclick="this.parentElement.parentElement.remove()"></button>
        </div>
    `;
    document.body.appendChild(toast);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        toast.remove();
    }, 5000);
}

// Error handler
function handleError(error, context = '') {
    console.error(`Error ${context}:`, error);
    showToast(`Ошибка ${context}: ${error.message}`, 'danger');
}

// Loading state helpers
function showLoading(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = `
            <div class="text-center py-5">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Загрузка...</span>
                </div>
            </div>
        `;
    }
}

function hideLoading(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = '';
    }
}

// Export utility functions
window.utils = {
    formatDate,
    formatNumber,
    truncateText,
    showToast,
    handleError,
    showLoading,
    hideLoading
};

