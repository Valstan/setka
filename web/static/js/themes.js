/* Страница «Темы и доли»: план наполнения ленты против кандидатов и факта.
 *
 * Запись отсюда одна: доли тем (PUT). Она безопасна: доля — потолок в отборе,
 * публикацию она не запускает.
 *
 * Сумма долей НЕ приводится к 100 автоматически. Каждая доля — самостоятельный
 * потолок, движку сумма безразлична, а молча переписывать введённые владельцем
 * числа хуже, чем показать, что сумма не сошлась.
 *
 * «Привести к 100%» считает НА ЭКРАНЕ и ничего не пишет. До 2026-09-11 кнопка
 * звала POST /normalize, а тот работает с СОХРАНЁННЫМИ долями: владелец набрал
 * 130%, нажал её до сохранения и дважды получил «нечего нормализовать» — сервер
 * не видел того, что видел он. Теперь нормализуются ровно видимые числа, а
 * записываются той же кнопкой «Сохранить», что и всё остальное. Ручка
 * /normalize осталась для curl и памяток.
 *
 * Сетевой отказ говорится словами. 11.09 оператор увидел голое «Failed to
 * fetch»: ни что ничего не сохранилось, ни что делать дальше.
 */
(function () {
    "use strict";

    // fetch() отвергается TypeError-ом, когда ответа не было вовсе: обрыв связи,
    // сброс соединения, блокировка расширением браузера. HTTP-ошибка — это ответ,
    // она приходит с текстом сервера и обрабатывается отдельно.
    class NetworkError extends Error {}

    async function request(url, options) {
        try {
            return await fetch(url, options);
        } catch (e) {
            throw new NetworkError("нет связи с сервером");
        }
    }

    async function getJSON(url) {
        const r = await request(url, { headers: { Accept: "application/json" } });
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
    }

    async function sendJSON(url, method, payload) {
        const r = await request(url, {
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
    // Есть ли на экране правки, которых нет на сервере. Без этой отметки
    // нормализация «на экране» выглядела бы как уже применённая.
    let dirty = false;

    function setDirty(value) {
        dirty = value;
        document.getElementById("themes-dirty").classList.toggle("d-none", !value);
    }

    // На время запроса кнопки заблокированы: 11.09 «Привести к 100%» ушла на
    // сервер дважды с разницей в четыре секунды.
    function setBusy(busy) {
        ["themes-save", "themes-normalize", "themes-refresh"].forEach(function (id) {
            document.getElementById(id).disabled = busy;
        });
    }

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
                setDirty(true);
            });
        });
        document.querySelectorAll(".themes-number").forEach(function (input) {
            input.addEventListener("input", function () {
                const r = document.querySelector(
                    '.themes-range[data-theme="' + input.dataset.theme + '"]'
                );
                if (input.value !== "") r.value = input.value;
                renderSum();
                setDirty(true);
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
                setDirty(true);
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

    // Пропорционально, с округлением до десятых — как считал сервер. Темы «не
    // ограничивать» и с пустым полем не трогаем («без потолка» — не ноль
    // процентов), запрет (доля 0) остаётся нулём.
    function normalizeOnScreen() {
        const inputs = Array.prototype.filter.call(
            document.querySelectorAll(".themes-number"),
            function (input) {
                return !input.disabled && input.value !== "" && Number(input.value) > 0;
            }
        );
        const total = inputs.reduce(function (acc, input) {
            return acc + Number(input.value);
        }, 0);
        if (!inputs.length || total <= 0) return false;
        inputs.forEach(function (input) {
            const value = Math.round((Number(input.value) / total) * 1000) / 10;
            input.value = value;
            const range = document.querySelector(
                '.themes-range[data-theme="' + input.dataset.theme + '"]'
            );
            if (range) range.value = value;
        });
        return true;
    }

    async function load() {
        try {
            render(await getJSON("/api/theme-quotas/"));
            setDirty(false);
        } catch (e) {
            const why = e instanceof NetworkError
                ? "нет связи с сервером — нажмите «Обновить»"
                : e.message;
            document.getElementById("themes-body").innerHTML =
                '<tr><td colspan="4" class="text-danger p-3">Не удалось загрузить: ' +
                escapeHtml(why) + "</td></tr>";
        }
    }

    function note(text, ok) {
        const el = document.getElementById("themes-save-note");
        el.textContent = text;
        el.className = "small " + (ok ? "text-success" : "text-danger");
    }

    document.addEventListener("DOMContentLoaded", function () {
        load();
        document.getElementById("themes-refresh").addEventListener("click", async function () {
            if (dirty && !window.confirm("Есть несохранённые изменения. Отбросить их и загрузить заново?")) {
                return;
            }
            setBusy(true);
            try {
                await load();
            } finally {
                setBusy(false);
            }
        });
        document.getElementById("themes-save").addEventListener("click", async function () {
            setBusy(true);
            note("Сохраняю…", true);
            try {
                await sendJSON("/api/theme-quotas/", "PUT", { shares: collectShares() });
                note("Сохранено", true);
                setDirty(false);
                await load();
            } catch (e) {
                note(
                    e instanceof NetworkError
                        ? "Нет связи с сервером: доли НЕ сохранены. Значения на экране на месте — " +
                          "нажмите «Сохранить» ещё раз."
                        : "Не сохранено: " + e.message,
                    false
                );
            } finally {
                setBusy(false);
            }
        });
        document.getElementById("themes-normalize").addEventListener("click", function () {
            if (!normalizeOnScreen()) {
                note("Нечего приводить: ни у одной темы нет доли больше нуля", false);
                return;
            }
            renderSum();
            setDirty(true);
            note("Доли приведены к 100% на экране — нажмите «Сохранить», чтобы применить", true);
        });
    });
})();
