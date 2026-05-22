// region_new.js — wizard для создания нового региона + запуск discovery.

(function () {
    'use strict';

    const form = document.getElementById('region-new-form');
    const cityInput = document.getElementById('center-city-input');
    const suggestionsBox = document.getElementById('city-suggestions');
    const vkCityIdInput = document.getElementById('vk-city-id');
    const vkCityLabel = document.getElementById('vk-city-label');
    const clearCityBtn = document.getElementById('clear-city-btn');
    const createBtn = document.getElementById('create-btn');
    const statusEl = document.getElementById('status');

    let cityLookupTimer = null;

    // ─── city auto-resolve ───
    cityInput.addEventListener('input', () => {
        const q = cityInput.value.trim();
        // Если поле менялось — сбрасываем уже выбранный city_id.
        vkCityIdInput.value = '';
        vkCityLabel.value = '';

        if (cityLookupTimer) clearTimeout(cityLookupTimer);
        if (q.length < 2) {
            suggestionsBox.style.display = 'none';
            return;
        }
        cityLookupTimer = setTimeout(() => lookupCity(q), 300);
    });

    async function lookupCity(q) {
        try {
            const resp = await fetch(`/api/discovery/cities?q=${encodeURIComponent(q)}`);
            const data = await resp.json();
            renderSuggestions(data.items || []);
        } catch (e) {
            suggestionsBox.innerHTML = `<div class="list-group-item text-danger">Ошибка: ${e.message}</div>`;
            suggestionsBox.style.display = 'block';
        }
    }

    function renderSuggestions(items) {
        if (!items.length) {
            suggestionsBox.innerHTML = '<div class="list-group-item text-muted">Ничего не нашлось</div>';
            suggestionsBox.style.display = 'block';
            return;
        }
        suggestionsBox.innerHTML = items.slice(0, 20).map(it => {
            const sub = [it.area, it.region].filter(Boolean).join(' · ');
            return `<button type="button" class="list-group-item list-group-item-action"
                            data-id="${it.id}" data-title="${escapeHtml(it.title)}"
                            data-sub="${escapeHtml(sub)}">
                <strong>${escapeHtml(it.title)}</strong>
                ${sub ? `<small class="text-muted d-block">${escapeHtml(sub)}</small>` : ''}
            </button>`;
        }).join('');
        suggestionsBox.style.display = 'block';

        suggestionsBox.querySelectorAll('button[data-id]').forEach(btn => {
            btn.addEventListener('click', () => {
                vkCityIdInput.value = btn.dataset.id;
                vkCityLabel.value = btn.dataset.title + (btn.dataset.sub ? ` · ${btn.dataset.sub}` : '');
                suggestionsBox.style.display = 'none';
            });
        });
    }

    document.addEventListener('click', e => {
        if (!suggestionsBox.contains(e.target) && e.target !== cityInput) {
            suggestionsBox.style.display = 'none';
        }
    });

    clearCityBtn.addEventListener('click', () => {
        vkCityIdInput.value = '';
        vkCityLabel.value = '';
    });

    // ─── submit ───
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        statusEl.innerHTML = '';
        createBtn.disabled = true;
        createBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Создаю...';

        try {
            const fd = new FormData(form);
            const payload = {
                code: (fd.get('code') || '').trim(),
                name: (fd.get('name') || '').trim(),
                vk_city_id: vkCityIdInput.value ? parseInt(vkCityIdInput.value, 10) : null,
                center_city: cityInput.value.trim() || null,
                neighbors: (fd.get('neighbors') || '').trim() || null,
                vk_group_id: fd.get('vk_group_id') ? parseInt(fd.get('vk_group_id'), 10) : null,
            };

            // 1. Create region.
            setStatus('info', '⏳ Создаём регион в БД...');
            const regResp = await fetch('/api/regions/', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            if (!regResp.ok) {
                const err = await regResp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${regResp.status}`);
            }
            const region = await regResp.json();

            // 2. Trigger discovery.
            setStatus('info', `✓ Регион создан (id=${region.id}). 🔍 Запускаю VK-discovery (может занять до минуты)...`);
            const discResp = await fetch('/api/discovery/trigger', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({region_id: region.id}),
            });
            const discResult = await discResp.json();
            if (!discResp.ok) {
                throw new Error(discResult.detail || `Discovery failed HTTP ${discResp.status}`);
            }

            setStatus('success',
                `✅ Discovery завершён: найдено <strong>${discResult.found}</strong> групп, ` +
                `${discResult.inserted} новых кандидатов, ${discResult.refreshed} обновлено. ` +
                `<a class="alert-link" href="/regions/${payload.code}/discovery">Открыть список →</a>`);
            // Через 2 сек редиректим автоматически.
            setTimeout(() => {
                window.location.href = `/regions/${payload.code}/discovery`;
            }, 2000);
        } catch (e) {
            setStatus('danger', `❌ ${e.message}`);
            createBtn.disabled = false;
            createBtn.innerHTML = '<i class="bi bi-rocket-takeoff"></i> Создать регион и запустить discovery';
        }
    });

    function setStatus(level, html) {
        statusEl.innerHTML = `<div class="alert alert-${level}">${html}</div>`;
    }

    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }
})();
