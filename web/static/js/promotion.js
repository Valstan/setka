/* Раздел «Раскрутка»: состав районов, план пар, журнал действий, настройки.
 *
 * Этап 0 — только чтение. Единственные записи, доступные отсюда, — пересчёт
 * состава (пишет в свои таблицы и в local_hashtags) и сохранение настроек.
 * Ничего не публикуется в VK.
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
        if (!r.ok) throw new Error("HTTP " + r.status);
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

    function num(value) {
        return value === null || value === undefined ? "—" : String(value);
    }

    function delta(value) {
        if (value === null || value === undefined) return '<span class="text-muted">—</span>';
        if (value > 0) return '<span class="text-success">+' + value + "</span>";
        if (value < 0) return '<span class="text-danger">' + value + "</span>";
        return '<span class="text-muted">0</span>';
    }

    function tile(label, value, hint) {
        return (
            '<div class="col-6 col-md-3">' +
            '<div class="card h-100"><div class="card-body py-2 px-3">' +
            '<div class="text-muted small">' + escapeHtml(label) + "</div>" +
            '<div class="fs-4">' + escapeHtml(String(value)) + "</div>" +
            (hint ? '<div class="text-muted small">' + escapeHtml(hint) + "</div>" : "") +
            "</div></div></div>"
        );
    }

    function statusBadge(status) {
        if (!status) return '<span class="text-muted small">—</span>';
        const map = {
            active: "bg-primary",
            graduated: "bg-success",
            paused: "bg-secondary",
            published: "bg-success",
            dry_run: "bg-info text-dark",
            error: "bg-danger",
            skipped: "bg-secondary",
            pending: "bg-warning text-dark",
        };
        const cls = map[status] || "bg-secondary";
        return '<span class="badge ' + cls + '">' + escapeHtml(status) + "</span>";
    }

    let regionsCache = [];

    function renderRegions() {
        const body = document.getElementById("promo-regions-body");
        if (!body) return;
        const onlyGaps = document.getElementById("promo-only-gaps").checked;
        const rows = regionsCache.filter(function (r) {
            if (!r.is_active) return false;
            return onlyGaps ? r.hygiene.gaps.length > 0 : true;
        });

        if (!rows.length) {
            body.innerHTML =
                '<tr><td colspan="6" class="text-muted small">Пробелов не осталось.</td></tr>';
            return;
        }

        body.innerHTML = rows
            .map(function (r) {
                const gaps = r.hygiene.gaps.length
                    ? r.hygiene.gaps
                          .map(function (g) {
                              return '<span class="badge bg-warning text-dark me-1">' +
                                  escapeHtml(g) + "</span>";
                          })
                          .join("")
                    : '<span class="text-success small">всё на месте</span>';
                const name = r.url
                    ? '<a href="' + escapeHtml(r.url) + '" target="_blank" rel="noopener">' +
                      escapeHtml(r.name) + "</a>"
                    : escapeHtml(r.name);
                return (
                    "<tr>" +
                    "<td>" + name + ' <span class="text-muted small">' +
                    escapeHtml(r.code) + "</span></td>" +
                    '<td class="text-end">' + num(r.members) + "</td>" +
                    '<td class="text-end">' + delta(r.delta_7d) + "</td>" +
                    '<td class="text-end">' + delta(r.delta_30d) + "</td>" +
                    "<td>" + statusBadge(r.status) + "</td>" +
                    "<td>" + gaps + "</td>" +
                    "</tr>"
                );
            })
            .join("");
    }

    async function loadOverview() {
        const data = await getJSON("/api/promotion/overview");
        const tiles = document.getElementById("promo-tiles");
        tiles.innerHTML =
            tile("В раскрутке", data.enrolled, "районов сейчас") +
            tile("Выпустилось", data.graduated, "переросли порог") +
            tile("Действий за 7 дней", data.actions_last_7d, data.published_last_7d + " опубликовано") +
            tile("Вызовов VK за 7 дней", data.api_calls_last_7d, "потрачено модулем");

        const kill = document.getElementById("promo-kill");
        if (!data.module_enabled) {
            kill.classList.remove("d-none");
            kill.innerHTML =
                "<strong>Модуль выключен</strong> (PROMO_DISABLED). Состав и план считаются " +
                "и показываются, но ни одно действие не публикуется.";
        } else {
            kill.classList.add("d-none");
        }
        return data.settings;
    }

    async function loadRegions() {
        const data = await getJSON("/api/promotion/enrollments");
        regionsCache = data.regions || [];
        renderRegions();
    }

    async function loadPlan() {
        const data = await getJSON("/api/promotion/plan");
        const body = document.getElementById("promo-plan-body");
        const pairs = data.pairs || [];
        body.innerHTML = pairs.length
            ? pairs
                  .map(function (p) {
                      const hop = p.hop === 1 ? "сосед" : p.hop === 2 ? "через одного" : "область";
                      const token = p.donor_has_token
                          ? '<span class="badge bg-success ms-1">свой ключ</span>'
                          : '<span class="badge bg-warning text-dark ms-1">общий аккаунт</span>';
                      return (
                          "<tr><td>" + escapeHtml(p.donor_name) + token + "</td>" +
                          '<td class="text-end">' + num(p.donor_members) + "</td>" +
                          "<td>" + escapeHtml(p.target_name) + "</td>" +
                          '<td class="text-end">' + num(p.target_members) + "</td>" +
                          "<td>" + escapeHtml(hop) + "</td></tr>"
                      );
                  })
                  .join("")
            : '<tr><td colspan="5" class="text-muted small">Пар нет: либо никто не зачислен, ' +
              "либо все доноры уже отработали в этом слоте.</td></tr>";

        const orphans = data.orphans || [];
        const box = document.getElementById("promo-orphans");
        box.innerHTML = orphans.length
            ? orphans
                  .map(function (o) {
                      return (
                          '<div class="border-bottom py-1">' +
                          "<strong>" + escapeHtml(o.name) + "</strong> " +
                          '<span class="text-muted">(' + num(o.members) + ")</span> — " +
                          escapeHtml(o.reason) +
                          "</div>"
                      );
                  })
                  .join("")
            : "Все зачисленные районы кем-то представлены.";
    }

    async function loadJournal() {
        const data = await getJSON("/api/promotion/actions?limit=100");
        const body = document.getElementById("promo-journal-body");
        const rows = data.actions || [];
        body.innerHTML = rows.length
            ? rows
                  .map(function (a) {
                      const when = (a.published_at || a.planned_at || "").slice(0, 16).replace("T", " ");
                      const link = a.post_url
                          ? '<a href="' + escapeHtml(a.post_url) + '" target="_blank" rel="noopener">открыть</a>'
                          : '<span class="text-muted">—</span>';
                      const pair =
                          (a.donor_group_id ? escapeHtml(String(a.donor_group_id)) : "—") +
                          " → r" + escapeHtml(String(a.target_region_id));
                      return (
                          "<tr><td>" + escapeHtml(when) + "</td>" +
                          "<td>" + escapeHtml(a.channel) + "</td>" +
                          "<td>" + pair + "</td>" +
                          "<td>" + statusBadge(a.status) +
                          (a.error ? '<div class="text-danger small">' + escapeHtml(a.error) + "</div>" : "") +
                          "</td>" +
                          "<td>" + link + "</td>" +
                          '<td class="text-end">' + num(a.api_calls) + "</td></tr>"
                      );
                  })
                  .join("")
            : '<tr><td colspan="6" class="text-muted small">Действий пока нет — ' +
              "каналы ещё не включались.</td></tr>";
    }

    function fillSettings(s) {
        if (!s) return;
        document.getElementById("promo-threshold").value = s.threshold_members;
        document.getElementById("promo-graduate").value = s.graduate_members;
        document.getElementById("promo-donor-min").value = s.donor_min_members;
        document.getElementById("promo-per-day").value = s.max_actions_per_day;
        document.getElementById("promo-per-donor").value = s.max_per_donor_per_week;
        document.getElementById("promo-second-hop").checked = !!s.second_hop_enabled;
    }

    async function saveSettings() {
        const note = document.getElementById("promo-save-note");
        note.textContent = "Сохраняю…";
        try {
            const payload = {
                threshold_members: parseInt(document.getElementById("promo-threshold").value, 10),
                graduate_members: parseInt(document.getElementById("promo-graduate").value, 10),
                donor_min_members: parseInt(document.getElementById("promo-donor-min").value, 10),
                max_actions_per_day: parseInt(document.getElementById("promo-per-day").value, 10),
                max_per_donor_per_week: parseInt(document.getElementById("promo-per-donor").value, 10),
                second_hop_enabled: document.getElementById("promo-second-hop").checked,
            };
            const data = await sendJSON("/api/promotion/settings", "PUT", payload);
            fillSettings(data.settings);
            note.textContent = "Сохранено";
            await loadPlan();
        } catch (e) {
            note.textContent = "Не сохранилось: " + e.message;
        }
    }

    async function refreshAll() {
        try {
            const settings = await loadOverview();
            fillSettings(settings);
            await Promise.all([loadRegions(), loadPlan(), loadJournal()]);
        } catch (e) {
            const body = document.getElementById("promo-regions-body");
            if (body) {
                body.innerHTML =
                    '<tr><td colspan="6" class="text-danger small">Не удалось загрузить: ' +
                    escapeHtml(e.message) + "</td></tr>";
            }
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.getElementById("promo-refresh").addEventListener("click", refreshAll);
        document.getElementById("promo-only-gaps").addEventListener("change", renderRegions);
        document.getElementById("promo-save").addEventListener("click", saveSettings);
        document.getElementById("promo-sync").addEventListener("click", async function () {
            const btn = this;
            btn.disabled = true;
            btn.textContent = "Считаю…";
            try {
                await sendJSON("/api/promotion/enrollments/sync", "POST");
                await refreshAll();
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Пересчитать состав';
            }
        });
        refreshAll();
    });
})();
