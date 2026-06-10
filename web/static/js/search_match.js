/* Универсальный многоуровневый поиск (brain pool #035) — единый shared-модуль
 * для ВСЕХ полей поиска/фильтра UI. Одна точка настройки, единое поведение.
 *
 * Уровни (следующий включается, когда предыдущий дал ноль):
 *   1. substring  — введённая последовательность целиком в любом месте строки;
 *   2. subsequence — те же символы в том же порядке, но с разрывами («дв12»);
 *   3. fuzzy      — опечатки/перестановки (биграммный Dice по словам).
 * Плюс: многотокен AND (каждое слово запроса должно совпасть), нормализация
 * (регистр, ё→е, compact-номера: «240-1» ≡ «2401»), ранжирование
 * exact > prefix > word-prefix > substring > subsequence > fuzzy,
 * подсветка <mark>, автокоррекция раскладки RU↔EN при нуле результатов.
 *
 * Зависимостей нет. Использование:
 *   const res = searchMatch.rank('клуб малмыж', items, it => [it.name]);
 *   res.matches → [{item, rank, highlights: [ranges|null per field]}], отсортировано;
 *   res.layoutFixed → запрос был сконвертирован из другой раскладки.
 *   searchMatch.filter(q, items, getFields) → только items (порядок исходный).
 *   searchMatch.highlight(text, ranges) → HTML с <mark> (текст экранируется).
 */
(function () {
    'use strict';

    const FUZZY_THRESHOLD = 0.5;   // Dice-похожесть, ниже — не считаем «похожим»
    const FUZZY_MIN_TOKEN = 3;     // короче — fuzzy даёт мусор
    const SEPARATORS = /[\s\-./]/g;

    // ---------------------------------------------------------- нормализация

    // Длино-стабильная нормализация (индексы результата = индексам оригинала):
    // только lower + ё→е посимвольно — чтобы подсветка ложилась на оригинал.
    function normPreserve(s) {
        let out = '';
        for (const ch of String(s || '')) {
            const lower = ch.toLowerCase();
            out += (lower === 'ё') ? 'е' : lower;
        }
        return out;
    }

    function normQuery(s) {
        return normPreserve(s).trim().replace(/\s+/g, ' ');
    }

    // «Преимущественно цифровой» токен → компактная форма без разделителей
    // (240-1 ≡ 2401 ≡ 240 1). null — если токен не похож на номер.
    function compactNumber(token) {
        const core = token.replace(SEPARATORS, '');
        if (!core) return null;
        const digits = (core.match(/\d/g) || []).length;
        if (digits < 2 || digits * 2 < core.length) return null;
        return core;
    }

    function tokenize(q) {
        return normQuery(q).split(' ').filter(Boolean).map(t => ({
            text: t,
            compact: compactNumber(t),
        }));
    }

    // ---------------------------------------------------- раскладка RU↔EN

    const EN_ROW = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`";
    const RU_ROW = 'йцукенгшщзхъфывапролджэячсмитьбюё';
    const EN2RU = {};
    const RU2EN = {};
    for (let i = 0; i < EN_ROW.length; i++) {
        EN2RU[EN_ROW[i]] = RU_ROW[i];
        RU2EN[RU_ROW[i]] = EN_ROW[i];
    }

    // Конвертирует строку из «не той» раскладки; направление — по преобладающему
    // алфавиту запроса. Возвращает исходник, если конвертация ничего не меняет.
    function convertLayout(s) {
        const str = String(s || '').toLowerCase();
        let latin = 0;
        let cyr = 0;
        for (const ch of str) {
            if (/[a-z]/.test(ch)) latin++;
            else if (/[а-яё]/.test(ch)) cyr++;
        }
        const map = latin >= cyr ? EN2RU : RU2EN;
        let out = '';
        for (const ch of str) out += map[ch] || ch;
        return out;
    }

    // ------------------------------------------------------------- матчинг

    // Уровень 2: подпоследовательность (по порядку, с разрывами).
    // Возвращает массив индексов совпавших символов либо null.
    function subsequenceIndices(token, cand) {
        const idx = [];
        let pos = 0;
        for (const ch of token) {
            pos = cand.indexOf(ch, pos);
            if (pos === -1) return null;
            idx.push(pos);
            pos += 1;
        }
        return idx;
    }

    function bigrams(s) {
        const set = new Set();
        for (let i = 0; i < s.length - 1; i++) set.add(s.slice(i, i + 2));
        return set;
    }

    function diceSimilarity(a, b) {
        if (a.length < 2 || b.length < 2) return a === b ? 1 : 0;
        const A = bigrams(a);
        const B = bigrams(b);
        let inter = 0;
        A.forEach(g => { if (B.has(g)) inter++; });
        return (2 * inter) / (A.size + B.size);
    }

    // Уровень 3: fuzzy — лучший Dice токена против каждого слова кандидата.
    function fuzzyMatches(token, cand) {
        if (token.length < FUZZY_MIN_TOKEN) return false;
        const words = cand.split(/[\s\-./,()«»"]+/).filter(Boolean);
        words.push(cand);
        return words.some(w => diceSimilarity(token, w) >= FUZZY_THRESHOLD);
    }

    // Ранг совпадения токена в одном поле (меньше = точнее), null = нет.
    // 0 exact, 1 префикс строки, 2 префикс слова, 3 substring, 4 subsequence,
    // 5 fuzzy. ranges — [start,end) для подсветки (null, если нечего метить).
    function matchToken(tok, candNorm, candCompact) {
        const t = tok.text;
        const idx = candNorm.indexOf(t);
        if (idx !== -1) {
            let rank = 3;
            if (idx === 0) rank = (t.length === candNorm.length) ? 0 : 1;
            else if (/[\s\-./(«"]/.test(candNorm[idx - 1])) rank = 2;
            return { rank, ranges: [[idx, idx + t.length]] };
        }
        // substring по компактной форме номера («2401» ловит «240-1»)
        if (tok.compact && candCompact && candCompact.includes(tok.compact)) {
            return { rank: 3, ranges: null };
        }
        const sub = subsequenceIndices(t, candNorm);
        if (sub) {
            return { rank: 4, ranges: sub.map(i => [i, i + 1]) };
        }
        if (fuzzyMatches(t, candNorm)) return { rank: 5, ranges: null };
        return null;
    }

    // ---------------------------------------------------------- ранжирование

    function rankAgainstFields(tokens, fields) {
        const normFields = fields.map(f => normPreserve(f || ''));
        const compactFields = normFields.map(f => f.replace(SEPARATORS, ''));
        const perField = fields.map(() => []);
        let worst = -1;
        let sum = 0;
        for (const tok of tokens) {
            let best = null;
            let bestField = -1;
            for (let fi = 0; fi < normFields.length; fi++) {
                const m = matchToken(tok, normFields[fi], compactFields[fi]);
                if (m && (!best || m.rank < best.rank)) {
                    best = m;
                    bestField = fi;
                }
            }
            if (!best) return null;            // AND: токен не нашёлся нигде
            if (best.ranges) perField[bestField].push(...best.ranges);
            worst = Math.max(worst, best.rank);
            sum += best.rank;
        }
        return { worst, sum, perField };
    }

    function rank(query, items, getFields) {
        const out = { matches: [], layoutFixed: false, query: normQuery(query) };
        if (!out.query) {
            out.matches = items.map(item => ({ item, rank: 0, highlights: null }));
            return out;
        }
        let tokens = tokenize(out.query);
        let matches = collect(tokens);
        if (!matches.length) {
            const alt = convertLayout(out.query);
            if (alt !== out.query) {
                tokens = tokenize(alt);
                matches = collect(tokens);
                if (matches.length) {
                    out.layoutFixed = true;
                    out.query = alt;
                }
            }
        }
        matches.sort((a, b) =>
            a.score.worst - b.score.worst
            || a.score.sum - b.score.sum
            || a.minLen - b.minLen
            || a.alpha.localeCompare(b.alpha, 'ru'));
        out.matches = matches.map(m => ({
            item: m.item,
            rank: m.score.worst,
            highlights: m.score.perField.map(r => (r.length ? mergeRanges(r) : null)),
        }));
        return out;

        function collect(toks) {
            const acc = [];
            for (const item of items) {
                const fields = getFields(item).map(f => String(f == null ? '' : f));
                const score = rankAgainstFields(toks, fields);
                if (!score) continue;
                const lens = fields.filter(Boolean).map(f => f.length);
                acc.push({
                    item,
                    score,
                    minLen: lens.length ? Math.min(...lens) : 0,
                    alpha: fields[0] || '',
                });
            }
            return acc;
        }
    }

    // Удобный шорткат: только отфильтровать, сохранив исходный порядок списка
    // (для сгруппированных списков, где свой порядок важнее ранга).
    function filter(query, items, getFields) {
        const res = rank(query, items, getFields);
        const keep = new Set(res.matches.map(m => m.item));
        return items.filter(it => keep.has(it));
    }

    // ------------------------------------------------------------- подсветка

    function mergeRanges(ranges) {
        const sorted = ranges.slice().sort((a, b) => a[0] - b[0]);
        const out = [];
        for (const r of sorted) {
            const last = out[out.length - 1];
            if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
            else out.push([r[0], r[1]]);
        }
        return out;
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // ranges — из rank().highlights[fieldIndex]; null/[] → просто экранирование.
    function highlight(text, ranges) {
        const s = String(text == null ? '' : text);
        if (!ranges || !ranges.length) return escapeHtml(s);
        let html = '';
        let pos = 0;
        for (const [start, end] of ranges) {
            if (start >= s.length) break;
            html += escapeHtml(s.slice(pos, start));
            html += '<mark>' + escapeHtml(s.slice(start, Math.min(end, s.length))) + '</mark>';
            pos = Math.min(end, s.length);
        }
        return html + escapeHtml(s.slice(pos));
    }

    // Подсветка «на лету» для простых случаев: сама находит диапазоны запроса
    // в тексте (по лучшему полю не заморачиваемся — текст один).
    function highlightQuery(text, query) {
        const tokens = tokenize(query);
        if (!tokens.length) return escapeHtml(text);
        const norm = normPreserve(text || '');
        const compact = norm.replace(SEPARATORS, '');
        const ranges = [];
        for (const tok of tokens) {
            const m = matchToken(tok, norm, compact);
            if (m && m.ranges) ranges.push(...m.ranges);
        }
        return highlight(text, ranges.length ? mergeRanges(ranges) : null);
    }

    window.searchMatch = {
        rank,
        filter,
        highlight,
        highlightQuery,
        tokenize,
        convertLayout,
        normQuery,
        compactNumber,
        escapeHtml,
    };
})();
