/* Страница «Темы и доли»: план наполнения ленты против кандидатов и факта.
 *
 * Записи отсюда: доли тем (PUT) и приведение к 100% (POST /normalize). Обе
 * безопасны: доля — потолок в отборе, публикацию она не запускает.
 *
 * Сумма долей НЕ приводится к 100 автоматически. Каждая доля — самостоятельный
 * потолок, движку сумма безразлична, а молча переписывать введённые владельцем
 * числа хуже, чем показать, что сумма не сошлась.
 */
(function () {
    "use strict";

    async function getJSON(url) {
        const r = await fetch(url, { headers: { Accept: "application/json" } });
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
    }

    async function sendJSON(url, method, payload) {
        const r = await fetch(url, {
            method: method,
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: payload === undefined ? undefined : JSON.stringify(payload),
        });
        if (!r.ok) {
            let detail = "HTTP " + r.status;
            try {
                const body = await r.json();
                if (body && body.detail) detail = body.detail;
            } catch (e) {
                /* тело не JSON — оставляем код статуса */
            }
            throw new Error(detail);
        }
        return r.json();
    }

    function escapeHtml(s) {
        if (s === null || s === undefined) return "";
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    // Процент отсутствует, когда мерить ещё не по чему (журнал пуст первые сутки).
    // Это не ноль: ноль сказал бы «темы не было», а тут «нет знаменателя».
    function pct(value) {
        return value === null || value === undefined ? "—" : value + "%";
    }

    let state = { themes: [], window_hours: 24, candidates_days: 7 };

    function rowHtml(t) {
        const id = encodeURIComponent(t.theme);
        const unlimited = t.share_percent === null || t.share_percent === undefined;
        const value = unlimited ? "" : t.share_percent;

        const descr = t.description
            ? '<div class="small text-muted">' + escapeHtml(t.description) + "</div>"
            : "";
        const serviceBadge = t.is_service
            ? ' <span class="badge bg-secondary">служебная</span>'
            : "";
        const warn = t.unreachable
            ? '<div class="small text-warning-emphasis mt-1">' +
              '<i class="bi bi-exclamation-triangle"></i> источников не хватает: ' +
              "кандидатов " + pct(t.candidates_pct) + ", потолок эту тему не поднимет" +
              "</div>"
            : "";

        if (t.is_service) {
            return (
                "<tr>" +
                '<td><strong>' + escapeHtml(t.theme) + "</strong>" + serviceBadge + descr + "</td>" +
                '<td class="text-muted small">доля не назначается</td>' +
                '<td class="text-end">' + pct(t.candidates_pct) +
                ' <span class="text-muted small">(' + t.candidates_count + ")</span></td>" +
                '<td class="text-end">' + pct(t.published_pct) +
                ' <span class="text-muted small">(' + t.published_count + ")</span></td>" +
                "</tr>"
            );
        }

        return (
            "<tr>" +
            "<td><strong>" + escapeHtml(t.theme) + "</strong>" + descr + warn + "</td>" +
            "<td>" +
            '  <div class="d-flex align-items-center gap-2">' +
            '    <input type="range" class="form-range flex-grow-1 themes-range" min="0" max="100" step="1"' +
            '           data-theme="' + id + '" value="' + (unlimited ? 0 : value) + '"' +
            (unlimited ? " disabled" : "") + ">" +
            '    <input type="number" class="form-control form-control-sm themes-number" style="width: 5.5rem;"' +
            '           min="0" max="100" step="1" data-theme="' + id + '" value="' + value + '"' +
            (unlimited ? " disabled" : "") + ">" +
            "  </div>" +
            '  <div class="form-check form-check-inline mt-1">' +
            '    <input class="form-check-input themes-unlimited" type="checkbox" data-theme="' + id + '"' +
            (unlimited ? " checked" : "") + '>' +
            '    <label class="form-check-label small text-muted">не ограничивать</label>' +
            "  </div>" +
            "</td>" +
            '<td class="text-end">' + pct(t.candidates_pct) +
            ' <span class="text-muted small">(' + t.candidates_count + ")</span></td>" +
            '<td class="text-end">' + pct(t.published_pct) +
            ' <span class="text-muted small">(' + t.published_count + ")</span></td>" +
            "</tr>"
        );
    }

    function renderSum() {
        let sum = 0;
        document.querySelectorAll(".themes-number").forEach(function (input) {
            if (!input.disabled && input.value !== "") sum += Number(input.value);
        });
        const el = document.getElementById("themes-sum");
        const rounded = Math.round(sum * 10) / 10;
        el.textContent =
            "Распределено " + rounded + "% · окно факта " + state.window_hours +
            " ч · кандидаты за " + state.candidates_days + " дн." +
            (rounded === 100 ? "" : " (сумма не обязана равняться 100)");
    }

    function render(data) {
        state = data;
        document.getElementById("themes-body").innerHTML =
            data.themes.map(rowHtml).join("") ||
            '<tr><td colspan="4" class="text-muted p-3">Словарь тем пуст</td></tr>';

        const gate = document.getElementById("themes-gate");
        if (data.quota_enabled) {
            gate.classList.add("d-none");
        } else {
            // Показываем РЕАЛЬНОЕ состояние гейта: пока он снят, доли сохраняются,
            // но потолки в волне не применяются. Умолчание «включено» было бы
            // самой тихой ложью, какую эта страница может сказать.
            gate.textContent =
                "Потолки выключены (CLASSIFIER_THEME_QUOTA_ENABLED). Доли сохраняются и видны " +
                "здесь, но волна их пока не применяет. Запрет темы (доля 0) действует всегда.";
            gate.classList.remove("d-none");
        }
        bindRow();
        renderSum();
    }

    function bindRow() {
        document.querySelectorAll(".themes-range").forEach(function (range) {
            range.addEventListener("input", function () {
                const n = document.querySelector(
                    '.themes-number[data-theme="' + range.dataset.theme + '"]'
                );
                n.value = range.value;
                renderSum();
            });
        });
        document.querySelectorAll(".themes-number").forEach(function (input) {
            input.addEventListener("input", function () {
                const r = document.querySelector(
                    '.themes-range[data-theme="' + input.dataset.theme + '"]'
                );
                if (input.value !== "") r.value = input.value;
                renderSum();
            });
        });
        document.querySelectorAll(".themes-unlimited").forEach(function (box) {
            box.addEventListener("change", function () {
                const sel = '[data-theme="' + box.dataset.theme + '"]';
                const range = document.querySelector(".themes-range" + sel);
                const number = document.querySelector(".themes-number" + sel);
                range.disabled = box.checked;
                number.disabled = box.checked;
                if (box.checked) number.value = "";
                else if (number.value === "") number.value = range.value;
                renderSum();
            });
        });
    }

    function collectShares() {
        const shares = {};
        document.querySelectorAll(".themes-unlimited").forEach(function (box) {
            const theme = decodeURIComponent(box.dataset.theme);
            if (box.checked) {
                shares[theme] = null; // null = снять потолок, не «ноль процентов»
                return;
            }
            const number = document.querySelector(
                '.themes-number[data-theme="' + box.dataset.theme + '"]'
            );
            shares[theme] = number.value === "" ? null : Number(number.value);
        });
        return shares;
    }

    async function load() {
        try {
            render(await getJSON("/api/theme-quotas/"));
        } catch (e) {
            document.getElementById("themes-body").innerHTML =
                '<tr><td colspan="4" class="text-danger p-3">Не удалось загрузить: ' +
                escapeHtml(e.message) + "</td></tr>";
        }
    }

    function note(text, ok) {
        const el = document.getElementById("themes-save-note");
        el.textContent = text;
        el.className = "small " + (ok ? "text-success" : "text-danger");
    }

    document.addEventListener("DOMContentLoaded", function () {
        load();
        document.getElementById("themes-refresh").addEventListener("click", load);
        document.getElementById("themes-save").addEventListener("click", async function () {
            note("Сохраняю…", true);
            try {
                await sendJSON("/api/theme-quotas/", "PUT", { shares: collectShares() });
                note("Сохранено", true);
                await load();
            } catch (e) {
                note(e.message, false);
            }
        });
        document.getElementById("themes-normalize").addEventListener("click", async function () {
            note("Нормализую…", true);
            try {
                await sendJSON("/api/theme-quotas/normalize", "POST");
                note("Доли приведены к 100%", true);
                await load();
            } catch (e) {
                note(e.message, false);
            }
        });
    });
})();
