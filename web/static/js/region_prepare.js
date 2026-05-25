// region_prepare.js — настройка discovery перед запуском:
// localities (жёсткий фильтр релевантности) + keywords (поисковые слова).
//
// Flow:
//   1. На загрузке: GET /api/discovery/regions/{code}/config — заполнить
//      textarea'и и prompt-блоки текущими значениями.
//   2. OSM auto-suggest для localities: GET /api/discovery/osm-localities.
//   3. Clipboard copy для prompt-блоков.
//   4. PATCH /api/discovery/regions/{code}/config/{field} при «Сохранить».
//   5. POST /api/discovery/trigger при «Запустить discovery» → redirect.

(function () {
    'use strict';

    const code = window.REGION_CODE;
    if (!code) {
        console.error('REGION_CODE not set');
        return;
    }

    // ─── DOM ──────────────────────────────────────────────────────
    const regionInfo = document.getElementById('region-info');

    const osmDistrictInput = document.getElementById('osm-district');
    const osmFetchBtn = document.getElementById('osm-fetch-btn');
    const osmStatus = document.getElementById('osm-status');

    const locPrompt = document.getElementById('loc-prompt');
    const locPromptCopy = document.getElementById('loc-prompt-copy');
    const locInput = document.getElementById('localities-input');
    const locSaveBtn = document.getElementById('loc-save-btn');
    const locSaveStatus = document.getElementById('loc-save-status');

    const kwPrompt = document.getElementById('kw-prompt');
    const kwPromptCopy = document.getElementById('kw-prompt-copy');
    const kwInput = document.getElementById('keywords-input');
    const kwSaveBtn = document.getElementById('kw-save-btn');
    const kwSaveStatus = document.getElementById('kw-save-status');

    const runBtn = document.getElementById('run-discovery-btn');
    const discoveryStatus = document.getElementById('discovery-status');

    // State (заполняется из /config на старте).
    let regionMeta = {name: '', center_city: ''};

    // ─── Helpers ──────────────────────────────────────────────────
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    function flashStatus(el, level, html, persist = false) {
        el.innerHTML = `<span class="text-${level}">${html}</span>`;
        if (!persist) {
            setTimeout(() => { el.innerHTML = ''; }, 4000);
        }
    }

    async function copyToClipboard(text, btn) {
        try {
            await navigator.clipboard.writeText(text);
            const orig = btn.innerHTML;
            btn.innerHTML = '<i class="bi bi-check2"></i> Скопировано';
            btn.classList.add('btn-success');
            btn.classList.remove('btn-outline-secondary');
            setTimeout(() => {
                btn.innerHTML = orig;
                btn.classList.remove('btn-success');
                btn.classList.add('btn-outline-secondary');
            }, 1500);
        } catch (e) {
            alert('Не удалось скопировать в буфер: ' + e.message);
        }
    }

    function suggestOsmDistrict(centerCity) {
        // Эвристика — не пытаемся быть умными. «Тужа» → «Тужа район»,
        // юзер всё равно правит вручную перед запросом (правильное имя
        // в OSM может быть «Тужинский район», «Малмыжский район» и т.д.).
        return centerCity ? `${centerCity} район` : '';
    }

    function buildLocalitiesPrompt() {
        const district = osmDistrictInput.value.trim() || regionMeta.center_city || regionMeta.name;
        return (
            `Перечисли все населённые пункты ${district || '<название района>'}: ` +
            `города, ПГТ, сёла, деревни, посёлки. По одному названию на строку, ` +
            `без нумерации, без пояснений. Только русские названия. Источник — Википедия.`
        );
    }

    function buildKeywordsPrompt() {
        const district = regionMeta.name || regionMeta.center_city || '<район>';
        return (
            `Сгенерируй 15-20 русскоязычных ключевых слов для поиска VK-сообществ ` +
            `${district}: тематика (новости, ДТП, объявления, культура, спорт), ` +
            `специфические термины для региона (например «вятский», «уральский»), ` +
            `популярные нп района. По одному слову/фразе на строку.`
        );
    }

    function refreshPrompts() {
        locPrompt.value = buildLocalitiesPrompt();
        kwPrompt.value = buildKeywordsPrompt();
    }

    // ─── Initial load ─────────────────────────────────────────────
    async function loadConfig() {
        try {
            const resp = await fetch(`/api/discovery/regions/${encodeURIComponent(code)}/config`);
            if (!resp.ok) {
                throw new Error(`HTTP ${resp.status}`);
            }
            const data = await resp.json();
            regionMeta = {name: data.name || '', center_city: data.center_city || ''};
            regionInfo.innerHTML = `<strong>${escapeHtml(data.name)}</strong>` +
                (data.center_city ? ` · центр: ${escapeHtml(data.center_city)}` : '');
            locInput.value = (data.localities || []).join('\n');
            kwInput.value = (data.discovery_keywords || []).join('\n');
            osmDistrictInput.value = suggestOsmDistrict(data.center_city || '');
            refreshPrompts();
        } catch (e) {
            regionInfo.innerHTML = `<span class="text-danger">Ошибка загрузки региона: ${escapeHtml(e.message)}</span>`;
        }
    }

    // ─── OSM auto-suggest ─────────────────────────────────────────
    osmFetchBtn.addEventListener('click', async () => {
        const district = osmDistrictInput.value.trim();
        if (!district) {
            osmStatus.innerHTML = '<span class="text-warning">Укажи название района</span>';
            return;
        }
        osmFetchBtn.disabled = true;
        osmStatus.innerHTML = '<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>Запрашиваю OpenStreetMap…</span>';
        try {
            const resp = await fetch(`/api/discovery/osm-localities?district=${encodeURIComponent(district)}`);
            const data = await resp.json();
            if (!data.ok || !data.items.length) {
                osmStatus.innerHTML = '<span class="text-warning">OSM не нашёл нп — попробуй другое имя района (например «Тужинский район») или заполни вручную.</span>';
                return;
            }
            // Merge: добавляем те нп, которых ещё нет в textarea (case-insensitive).
            const existing = new Set(
                locInput.value.split('\n').map(s => s.trim().toLowerCase()).filter(Boolean)
            );
            const toAdd = data.items.filter(n => !existing.has(n.toLowerCase()));
            const merged = [
                ...locInput.value.split('\n').map(s => s.trim()).filter(Boolean),
                ...toAdd,
            ];
            locInput.value = merged.join('\n');
            osmStatus.innerHTML = `<span class="text-success">OSM нашёл ${data.items.length}, добавлено новых: ${toAdd.length}.</span>`;
        } catch (e) {
            osmStatus.innerHTML = `<span class="text-danger">Ошибка: ${escapeHtml(e.message)}</span>`;
        } finally {
            osmFetchBtn.disabled = false;
        }
    });

    // Prompts зависят от osm-district input — обновляем live.
    osmDistrictInput.addEventListener('input', refreshPrompts);

    // ─── Clipboard ────────────────────────────────────────────────
    locPromptCopy.addEventListener('click', () => copyToClipboard(locPrompt.value, locPromptCopy));
    kwPromptCopy.addEventListener('click', () => copyToClipboard(kwPrompt.value, kwPromptCopy));

    // ─── Save ─────────────────────────────────────────────────────
    async function saveField(field, textareaEl, statusEl, btnEl) {
        const value = textareaEl.value;
        btnEl.disabled = true;
        statusEl.innerHTML = '<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>Сохраняю…</span>';
        try {
            const resp = await fetch(
                `/api/discovery/regions/${encodeURIComponent(code)}/config/${field}`,
                {
                    method: 'PATCH',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({value}),
                }
            );
            const data = await resp.json();
            if (!resp.ok) {
                throw new Error(data.detail || `HTTP ${resp.status}`);
            }
            // Replace textarea with parsed canonical form (dedup + trim).
            textareaEl.value = (data.items || []).join('\n');
            flashStatus(statusEl, 'success', `✓ Сохранено: ${data.count} элемент(ов)`);
        } catch (e) {
            flashStatus(statusEl, 'danger', `❌ ${escapeHtml(e.message)}`, true);
        } finally {
            btnEl.disabled = false;
        }
    }

    locSaveBtn.addEventListener('click', () => saveField('localities', locInput, locSaveStatus, locSaveBtn));
    kwSaveBtn.addEventListener('click', () => saveField('discovery_keywords', kwInput, kwSaveStatus, kwSaveBtn));

    // ─── Run discovery ────────────────────────────────────────────
    runBtn.addEventListener('click', async () => {
        // Сначала автосохраним оба поля (если юзер забыл).
        await saveField('localities', locInput, locSaveStatus, locSaveBtn);
        await saveField('discovery_keywords', kwInput, kwSaveStatus, kwSaveBtn);

        runBtn.disabled = true;
        runBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Запускаю…';
        discoveryStatus.innerHTML = '<div class="alert alert-info">🔍 Discovery идёт (30-120 сек)…</div>';
        try {
            // /trigger ожидает region_id, не code — придётся сначала узнать id.
            const regResp = await fetch('/api/regions/');
            const regData = await regResp.json();
            const list = Array.isArray(regData) ? regData : (regData.regions || []);
            const region = list.find(r => r.code === code);
            if (!region) throw new Error(`Регион "${code}" не найден`);

            const discResp = await fetch('/api/discovery/trigger', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({region_id: region.id}),
            });
            const discData = await discResp.json();
            if (!discResp.ok) {
                throw new Error(discData.detail || `HTTP ${discResp.status}`);
            }
            discoveryStatus.innerHTML = `<div class="alert alert-success">✅ Найдено <strong>${discData.found}</strong> кандидатов после фильтра релевантности. Открываю список…</div>`;
            setTimeout(() => {
                window.location.href = `/regions/${encodeURIComponent(code)}/discovery`;
            }, 1500);
        } catch (e) {
            discoveryStatus.innerHTML = `<div class="alert alert-danger">❌ ${escapeHtml(e.message)}</div>`;
            runBtn.disabled = false;
            runBtn.innerHTML = '<i class="bi bi-search"></i> Запустить discovery';
        }
    });

    // Kickoff.
    loadConfig();
})();
