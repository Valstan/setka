// region_prepare.js — настройка discovery перед запуском:
// localities (жёсткий фильтр релевантности) + keywords (поисковые слова).
//
// Flow:
//   1. На загрузке: GET /api/discovery/regions/{code}/config — заполнить
//      textarea'и и prompt-блоки текущими значениями.
//   2. Clipboard copy для двух prompt-блоков (localities + keywords).
//   3. PATCH /api/discovery/regions/{code}/config/{field} при «Сохранить».
//   4. POST /api/discovery/trigger при «Запустить discovery» → redirect.
//
// 2026-05-25: OSM Overpass auto-suggest удалён — не находил мелкие
// районы (Тужа, Тужинский), нейросеть по clipboard-prompt'у даёт
// результат лучше и стабильнее.

(function () {
    'use strict';

    const code = window.REGION_CODE;
    if (!code) {
        console.error('REGION_CODE not set');
        return;
    }

    // ─── DOM ──────────────────────────────────────────────────────
    const regionInfo = document.getElementById('region-info');

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

    function getCurrentLocalities() {
        return locInput.value.split('\n').map(s => s.trim()).filter(Boolean);
    }

    function buildLocalitiesPrompt() {
        // 2026-05-25: источник — ОКТМО (Росстат).
        // 2026-05-26: добавлено явное правило про омонимные топонимы.
        // Backend использует список как substring-фильтр по корню слова
        // (стем). Если нп называется «Свобода», «Сороки», «Лоскуты», его
        // стем матчит «свобода»/«40 сороков»/«лоскутное шитьё» в тысячах
        // нерелевантных пабликов. Лучше потерять 5-10 мелких деревень с
        // подозрительными названиями, чем добавить 200+ мусорных кандидатов
        // в БД (реальные инциденты на tuzha и verkhoshizheme 2026-05-25/26).
        const district = regionMeta.name || regionMeta.center_city || '<название района>';
        return (
            `Перечисли населённые пункты муниципального района «${district}»: ` +
            `города, ПГТ, сёла, деревни, посёлки, починки, хутора. ` +
            `Источник — ОКТМО (Общероссийский классификатор территорий ` +
            `муниципальных образований Росстата), сверь со справочником на ` +
            `classinform.ru/oktmo.\n\n` +
            `❗ ВАЖНО — ИСКЛЮЧИ из ответа населённые пункты, чьё название ` +
            `может быть омонимом обычного слова. Программа использует список ` +
            `как substring-фильтр по корню слова, и такие топонимы дают ` +
            `сотни ложно-релевантных групп в поиске ВКонтакте.\n\n` +
            `Не включай нп, чьё название:\n` +
            `• совпадает с обычным русским словом: «Свобода», «Сороки», ` +
            `«Песок», «Ключи», «Тайник», «Жёлтые», «Лена», «Ворон/Воронье», ` +
            `«Кручина», «Сутяга», «Кикиморки», «Угор», «Лес», «Поле», «Дом»;\n` +
            `• совпадает с фамилией: «Фомино», «Самсоны», «Соболи», ` +
            `«Михайлово», «Иваново», «Петрово», «Сидорово»;\n` +
            `• совпадает с предметом быта или ремеслом: «Лоскуты», ` +
            `«Коробки», «Чугуны», «Корбки», «Котлы», «Гвозди».\n\n` +
            `Если у тебя есть сомнения насчёт конкретного нп — НЕ ВКЛЮЧАЙ. ` +
            `Лучше пропустить 5-10 мелких деревень, чем породить мусор в БД.\n\n` +
            `Формат ответа — без нумерации, по одному названию на строку, ` +
            `только русские названия. Не добавляй пояснения и заголовки.`
        );
    }

    function buildKeywordsPrompt() {
        // 2026-05-25: prompt значительно расширен по smoke-feedback — был
        // слишком узкий (5 категорий), нейросетям нечем «думать». Теперь
        // явно перечисляем направления + подмешиваем текущий список нп,
        // чтобы предложения были привязаны к конкретным локалитетам района.
        const district = regionMeta.name || regionMeta.center_city || '<район>';
        const localities = getCurrentLocalities();
        const localitiesBlock = localities.length
            ? `\n\nНаселённые пункты района (используй их в ключевиках где уместно, ` +
              `например «новости Шешурга», «ДТП Тужа»):\n${localities.slice(0, 40).join(', ')}.`
            : '';
        return (
            `Сгенерируй 30-50 русскоязычных ключевых слов и коротких фраз для поиска ` +
            `VK-сообществ муниципального района «${district}». Цель — найти все ` +
            `тематические паблики района через VK groups.search.\n\n` +
            `Покрой максимум направлений (по 3-5 ключевиков на каждое):\n` +
            `• новости и СМИ (новости, газета, ТВ, радио, события);\n` +
            `• объявления и торговля (объявления, барахолка, куплю/продам, отдам даром, авто, недвижимость);\n` +
            `• происшествия (ДТП, ЧП, происшествия, аварии, потеряшки, розыск, помощь);\n` +
            `• ЖКХ и инфраструктура (ЖКХ, дороги, отключения, газ, вода, электричество, благоустройство);\n` +
            `• власть и сервисы (администрация, депутаты, госуслуги, МФЦ);\n` +
            `• образование (школа, лицей, гимназия, колледж, детсад, родители);\n` +
            `• спорт (спорт, секция, тренер, ФОК, стадион, рыбалка, охота);\n` +
            `• культура и досуг (культура, библиотека, музей, ДК, афиша, концерт, кино);\n` +
            `• здоровье (поликлиника, больница, аптека, врач);\n` +
            `• соседские/локальные чаты (соседи, чат, подслушано, типичный, инсайд);\n` +
            `• работа (работа, вакансии, трудоустройство);\n` +
            `• сельское хозяйство и природа (огород, рассада, грибы, лес, рыбалка) — если район сельский.\n\n` +
            `Также добавь специфические для региона слова (диалектные топонимы, ` +
            `сокращения, прозвища — типа «вятский», «уральский», «казанский»).` +
            localitiesBlock + `\n\n` +
            `Формат ответа — СТРОГО одно слово/короткая фраза на строку, без нумерации, ` +
            `без категорий-заголовков, без пояснений. Только сами ключевики.`
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
            refreshPrompts();
        } catch (e) {
            regionInfo.innerHTML = `<span class="text-danger">Ошибка загрузки региона: ${escapeHtml(e.message)}</span>`;
        }
    }

    // Live-обновление keywords prompt'а: при правке списка нп меняется
    // подмешанный в prompt блок локалитетов.
    locInput.addEventListener('input', () => {
        kwPrompt.value = buildKeywordsPrompt();
    });

    // ─── Clipboard ────────────────────────────────────────────────
    locPromptCopy.addEventListener('click', () => copyToClipboard(locPrompt.value, locPromptCopy));
    kwPromptCopy.addEventListener('click', () => copyToClipboard(kwPrompt.value, kwPromptCopy));

    // ─── Save ─────────────────────────────────────────────────────
    async function saveField(field, textareaEl, statusEl, btnEl) {
        const value = textareaEl.value;
        btnEl.disabled = true;
        statusEl.innerHTML = '<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>Сохраняю…</span>';
        const url = `/api/discovery/regions/${encodeURIComponent(code)}/config/${field}`;
        try {
            const resp = await fetch(url, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({value}),
            });
            // Расширенная диагностика 2026-05-25 (smoke-feedback «Failed to fetch»):
            // печатаем response в console, чтобы при повторе бага была видна
            // полная картина (status, content-type, тело).
            console.log(`[saveField/${field}] HTTP ${resp.status} ${resp.statusText}`,
                        {url, contentType: resp.headers.get('content-type')});
            if (!resp.ok) {
                let detail;
                try {
                    const data = await resp.json();
                    detail = data.detail || JSON.stringify(data);
                } catch {
                    detail = (await resp.text()).slice(0, 200) || `HTTP ${resp.status}`;
                }
                throw new Error(`HTTP ${resp.status}: ${detail}`);
            }
            const data = await resp.json();
            // Replace textarea with parsed canonical form (dedup + trim).
            textareaEl.value = (data.items || []).join('\n');
            // Обновим keywords-prompt, если только что сохранили localities —
            // в нём встроен текущий список нп.
            if (field === 'localities') {
                kwPrompt.value = buildKeywordsPrompt();
            }
            flashStatus(statusEl, 'success', `✓ Сохранено: ${data.count} элемент(ов)`);
        } catch (e) {
            // TypeError "Failed to fetch" = network-level. Подскажем
            // юзеру, что смотреть, а в консоль печатаем полный объект.
            console.error(`[saveField/${field}] failed:`, e, {url});
            const isNetwork = e.name === 'TypeError';
            const hint = isNetwork
                ? `❌ Сетевая ошибка (${escapeHtml(e.message)}). Открой DevTools → Console, увидишь подробности. Попробуй ещё раз через 5 сек.`
                : `❌ ${escapeHtml(e.message)}`;
            flashStatus(statusEl, 'danger', hint, true);
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
