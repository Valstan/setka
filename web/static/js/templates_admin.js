// Message templates CRUD page (etap 4b)
// Browser-side controller for /templates.

document.addEventListener('DOMContentLoaded', () => loadTemplates());

async function loadTemplates() {
    const loading = document.getElementById('templates-loading');
    const empty = document.getElementById('templates-empty');
    const table = document.getElementById('templates-table');
    const tbody = document.getElementById('templates-tbody');

    loading.style.display = 'block';
    empty.style.display = 'none';
    table.style.display = 'none';

    try {
        const resp = await fetch('/api/templates/?include_inactive=1');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const items = data.templates || [];

        if (items.length === 0) {
            loading.style.display = 'none';
            empty.style.display = 'block';
            return;
        }

        tbody.innerHTML = items.map(t => `
            <tr>
                <td><span class="badge bg-light text-dark">${escapeHtml(t.category || '—')}</span></td>
                <td><strong>${escapeHtml(t.title)}</strong></td>
                <td class="small text-muted" style="white-space: pre-wrap;">${escapeHtml((t.body || '').slice(0, 200))}${t.body && t.body.length > 200 ? '…' : ''}</td>
                <td>${t.is_active
                    ? '<span class="badge bg-success">активен</span>'
                    : '<span class="badge bg-secondary">скрыт</span>'}</td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-secondary" onclick='editTemplate(${JSON.stringify(t)})'>
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteTemplate(${t.id})">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            </tr>
        `).join('');

        loading.style.display = 'none';
        table.style.display = '';
    } catch (e) {
        loading.style.display = 'none';
        alert(`Ошибка загрузки: ${e.message}`);
    }
}

function openTemplateEditor() {
    document.getElementById('template-modal-title').textContent = 'Новый шаблон';
    document.getElementById('tpl-id').value = '';
    document.getElementById('tpl-title').value = '';
    document.getElementById('tpl-category').value = '';
    document.getElementById('tpl-body').value = '';
    document.getElementById('tpl-is-active').checked = true;
    document.getElementById('tpl-status').textContent = '';
    bootstrap.Modal.getOrCreateInstance(document.getElementById('template-modal')).show();
}

function editTemplate(tpl) {
    document.getElementById('template-modal-title').textContent = 'Редактирование шаблона';
    document.getElementById('tpl-id').value = tpl.id;
    document.getElementById('tpl-title').value = tpl.title || '';
    document.getElementById('tpl-category').value = tpl.category || '';
    document.getElementById('tpl-body').value = tpl.body || '';
    document.getElementById('tpl-is-active').checked = !!tpl.is_active;
    document.getElementById('tpl-status').textContent = '';
    bootstrap.Modal.getOrCreateInstance(document.getElementById('template-modal')).show();
}

async function saveTemplate() {
    const id = document.getElementById('tpl-id').value;
    const title = document.getElementById('tpl-title').value.trim();
    const body = document.getElementById('tpl-body').value.trim();
    const category = document.getElementById('tpl-category').value.trim() || null;
    const is_active = document.getElementById('tpl-is-active').checked;
    const status = document.getElementById('tpl-status');

    if (!title || !body) {
        status.className = 'mt-2 small text-danger';
        status.textContent = 'Название и текст обязательны';
        return;
    }

    const url = id ? `/api/templates/${id}` : '/api/templates/';
    const method = id ? 'PUT' : 'POST';

    try {
        const resp = await fetch(url, {
            method,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title, body, category, is_active}),
        });
        if (!resp.ok) {
            const txt = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${txt}`);
        }
        bootstrap.Modal.getInstance(document.getElementById('template-modal')).hide();
        await loadTemplates();
    } catch (e) {
        status.className = 'mt-2 small text-danger';
        status.textContent = `Не сохранилось: ${e.message}`;
    }
}

async function deleteTemplate(id) {
    if (!confirm('Удалить шаблон?')) return;
    try {
        const resp = await fetch(`/api/templates/${id}`, {method: 'DELETE'});
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        await loadTemplates();
    } catch (e) {
        alert(`Не удалось удалить: ${e.message}`);
    }
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
