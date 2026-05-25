// region_new.js — wizard для создания нового региона + запуск discovery.
// Две входных поля: "Название района и область" (с auto-resolve VK city) и
// "Главная группа региона" (URL → resolveScreenName). Регион создаётся как
// черновик (is_active=false), discovery запускается синхронно, потом редирект
// на страницу проверки кандидатов.

(function () {
    'use strict';

    const form = document.getElementById('region-new-form');
    const fullNameInput = document.getElementById('full-name-input');
    const suggestionsBox = document.getElementById('city-suggestions');
    const vkCityIdInput = document.getElementById('vk-city-id');
    const centerCityHidden = document.getElementById('center-city-hidden');
    const codeHidden = document.getElementById('code-hidden');

    const vkGroupUrlInput = document.getElementById('vk-group-url');
    const vkGroupPreview = document.getElementById('vk-group-preview');
    const vkGroupIdHidden = document.getElementById('vk-group-id-hidden');

    const createBtn = document.getElementById('create-btn');
    const statusEl = document.getElementById('status');

    let cityLookupTimer = null;

    // ─── Транслитерация кириллицы для slug (зеркало utils/translit.py) ───
    const CYR_TO_LAT = {
        'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z',
        'и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r',
        'с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh',
        'щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
    };

    function slugifyCyrillic(text) {
        if (!text) return '';
        const lower = text.trim().toLowerCase();
        let out = '';
        for (const ch of lower) {
            out += (ch in CYR_TO_LAT) ? CYR_TO_LAT[ch] : ch;
        }
        return out.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    }

    function extractCenterCity(fullName) {
        // "Карачев, Брянская область" → "Карачев"
        return (fullName.split(',')[0] || '').trim();
    }

    // ─── city auto-resolve ───
    fullNameInput.addEventListener('input', () => {
        const q = extractCenterCity(fullNameInput.value);
        // На каждом input сбрасываем уже выбранный city_id — пользователь возможно
        // меняет название.
        vkCityIdInput.value = '';
        centerCityHidden.value = q;
        codeHidden.value = slugifyCyrillic(q);
        updateCreateButton();

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
            suggestionsBox.innerHTML = '<div class="list-group-item text-muted">Город не найден в VK — продолжим без гео-поиска</div>';
            suggestionsBox.style.display = 'block';
            return;
        }
        suggestionsBox.innerHTML = items.slice(0, 20).map(it => {
            const sub = [it.area, it.region].filter(Boolean).join(' · ');
            return `<button type="button" class="list-group-item list-group-item-action"
                            data-id="${it.id}"
                            data-title="${escapeHtml(it.title)}"
                            data-region="${escapeHtml(it.region || '')}"
                            data-sub="${escapeHtml(sub)}">
                <strong>${escapeHtml(it.title)}</strong>
                ${sub ? `<small class="text-muted d-block">${escapeHtml(sub)}</small>` : ''}
            </button>`;
        }).join('');
        suggestionsBox.style.display = 'block';

        suggestionsBox.querySelectorAll('button[data-id]').forEach(btn => {
            btn.addEventListener('click', () => {
                vkCityIdInput.value = btn.dataset.id;
                const composed = btn.dataset.region
                    ? `${btn.dataset.title}, ${btn.dataset.region}`
                    : btn.dataset.title;
                fullNameInput.value = composed;
                centerCityHidden.value = btn.dataset.title;
                codeHidden.value = slugifyCyrillic(btn.dataset.title);
                suggestionsBox.style.display = 'none';
                updateCreateButton();
            });
        });
    }

    document.addEventListener('click', e => {
        if (!suggestionsBox.contains(e.target) && e.target !== fullNameInput) {
            suggestionsBox.style.display = 'none';
        }
    });

    // ─── Главная группа: resolve VK URL на blur ───
    vkGroupUrlInput.addEventListener('blur', resolveVkGroup);
    vkGroupUrlInput.addEventListener('change', resolveVkGroup);

    let lastResolved = '';
    async function resolveVkGroup() {
        const url = vkGroupUrlInput.value.trim();
        if (!url) {
            vkGroupPreview.textContent = '';
            vkGroupIdHidden.value = '';
            updateCreateButton();
            return;
        }
        if (url === lastResolved) return;
        lastResolved = url;
        vkGroupPreview.innerHTML = '<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>Проверяю VK…</span>';
        vkGroupIdHidden.value = '';
        updateCreateButton();
        try {
            const resp = await fetch(`/api/discovery/resolve-vk-url?url=${encodeURIComponent(url)}`);
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({detail: `HTTP ${resp.status}`}));
                vkGroupPreview.innerHTML = `<span class="text-danger">❌ ${escapeHtml(err.detail || 'не распознано')}</span>`;
                return;
            }
            const data = await resp.json();
            // backend возвращает group_id положительный — мы храним так же как Region.vk_group_id (см. модель)
            vkGroupIdHidden.value = String(data.group_id);
            vkGroupPreview.innerHTML = `<span class="text-success">✓ Найдено:</span> <strong>${escapeHtml(data.name)}</strong>` +
                (data.members_count ? ` <span class="text-muted">· ${data.members_count.toLocaleString('ru-RU')} подписчиков</span>` : '');
        } catch (e) {
            vkGroupPreview.innerHTML = `<span class="text-danger">❌ ${escapeHtml(e.message)}</span>`;
        } finally {
            updateCreateButton();
        }
    }

    function updateCreateButton() {
        const hasName = !!(fullNameInput.value || '').trim();
        const hasCode = !!(codeHidden.value || '').trim();
        const hasGroup = !!(vkGroupIdHidden.value || '').trim();
        createBtn.disabled = !(hasName && hasCode && hasGroup);
    }

    // ─── submit ───
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        statusEl.innerHTML = '';
        createBtn.disabled = true;
        createBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Создаю...';

        try {
            const payload = {
                code: codeHidden.value.trim(),
                name: fullNameInput.value.trim(),
                center_city: centerCityHidden.value.trim() || null,
                vk_city_id: vkCityIdInput.value ? parseInt(vkCityIdInput.value, 10) : null,
                vk_group_id: parseInt(vkGroupIdHidden.value, 10),
                is_active: false,
            };
            if (!payload.code) throw new Error('Не удалось сгенерировать код региона из названия');
            if (!Number.isFinite(payload.vk_group_id)) throw new Error('Главная VK-группа не распознана');

            // 1. Create region (draft). Если черновик уже создан (предыдущая
            // попытка свалилась после INSERT regions, но до завершения discovery)
            // — спросить пользователя и продолжить с существующим.
            setStatus('info', '⏳ Создаём черновик региона...');
            const regResp = await fetch('/api/regions/', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            let region;
            if (regResp.status === 400) {
                const err = await regResp.json().catch(() => ({}));
                const msg = (err.detail || '').toLowerCase();
                if (msg.includes('already exists') || msg.includes('уже существует')) {
                    const existing = await findRegionByCode(payload.code);
                    if (existing && !existing.is_active) {
                        const ok = confirm(
                            `Регион «${payload.code}» уже создан как черновик (предыдущая попытка не довела до конца).\n\n` +
                            `Перейти к подготовке discovery (localities/keywords) для этого черновика?`
                        );
                        if (ok) {
                            window.location.href = `/regions/${payload.code}/prepare`;
                            return;
                        }
                        throw new Error('Создание отменено — черновик уже существует.');
                    }
                    if (existing && existing.is_active) {
                        throw new Error(`Регион «${payload.code}» уже активен в Сетке.`);
                    }
                }
                throw new Error(err.detail || `HTTP 400`);
            }
            if (!regResp.ok) {
                const err = await regResp.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${regResp.status}`);
            }
            region = await regResp.json();

            // 2. Redirect на подготовку — там юзер заполнит localities/keywords
            //    и САМ нажмёт «Запустить discovery». Это даёт фильтру релевантности
            //    шанс сработать, иначе при пустом config выскакивают крупные
            //    общегородские паблики (см. PR 1 серии итерации 3).
            setStatus('success',
                `✓ Черновик создан (код=${payload.code}). Открываю подготовку discovery…`);
            setTimeout(() => {
                window.location.href = `/regions/${payload.code}/prepare`;
            }, 800);
        } catch (e) {
            setStatus('danger', `❌ ${e.message}`);
            createBtn.disabled = false;
            createBtn.innerHTML = '<i class="bi bi-rocket-takeoff"></i> Создать новый регион';
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

    async function findRegionByCode(code) {
        try {
            const resp = await fetch('/api/regions/');
            if (!resp.ok) return null;
            const data = await resp.json();
            const list = Array.isArray(data) ? data : (data.regions || []);
            return list.find(r => r.code === code) || null;
        } catch (_e) {
            return null;
        }
    }
})();
