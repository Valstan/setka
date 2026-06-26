// Статистика использования VK-шлюза (/gateway-stats).
// Тянет /api/gateway-stats/{summary,timeline,recent}; рисует таблицы + график.

(function () {
  "use strict";

  let chart = null;

  function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function statusBadge(row) {
    if (row.status === 200 && row.ok) return '<span class="badge bg-success">ok</span>';
    if (row.status === 200 && !row.ok) {
      const code = row.error_code ? " " + row.error_code : "";
      return '<span class="badge bg-warning text-dark">VK err' + code + "</span>";
    }
    return '<span class="badge bg-danger">' + row.status + "</span>";
  }

  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  function days() {
    return document.getElementById("gw-days").value;
  }

  async function loadSummary() {
    const data = await getJSON("/api/gateway-stats/summary?days=" + days());
    document.getElementById("gw-total").textContent =
      "— всего " + data.total + " запросов за " + data.days + " дн.";
    const body = document.getElementById("gw-summary-body");
    if (!data.projects.length) {
      body.innerHTML = '<tr><td colspan="5" class="text-muted small">Пока никто не пользовался.</td></tr>';
      return;
    }
    body.innerHTML = data.projects
      .map(
        (p) =>
          "<tr><td><strong>" +
          escapeHtml(p.project) +
          "</strong></td><td class='text-end'>" +
          p.total +
          "</td><td class='text-end text-success'>" +
          p.ok +
          "</td><td class='text-end " +
          (p.errors ? "text-danger" : "text-muted") +
          "'>" +
          p.errors +
          "</td><td>" +
          fmtTime(p.last_used) +
          "</td></tr>"
      )
      .join("");
  }

  async function loadTimeline() {
    const data = await getJSON("/api/gateway-stats/timeline?days=" + days());
    const empty = document.getElementById("gw-timeline-empty");
    const canvas = document.getElementById("gw-chart");
    if (!data.points.length) {
      empty.classList.remove("d-none");
      canvas.classList.add("d-none");
      if (chart) {
        chart.destroy();
        chart = null;
      }
      return;
    }
    empty.classList.add("d-none");
    canvas.classList.remove("d-none");
    const labels = data.points.map((p) => p.day);
    const values = data.points.map((p) => p.total);
    if (chart) chart.destroy();
    chart = new Chart(canvas, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [{ label: "Запросов", data: values, backgroundColor: "#0d6efd" }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  async function loadRecent() {
    const filter = document.getElementById("gw-filter").value.trim();
    const url = "/api/gateway-stats/recent?limit=100" + (filter ? "&project=" + encodeURIComponent(filter) : "");
    const data = await getJSON(url);
    const body = document.getElementById("gw-recent-body");
    if (!data.items.length) {
      body.innerHTML = '<tr><td colspan="5" class="text-muted small">Нет запросов.</td></tr>';
      return;
    }
    body.innerHTML = data.items
      .map((row) => {
        let params = "";
        try {
          params = JSON.stringify(row.params || {});
        } catch (e) {
          params = "";
        }
        if (params.length > 160) params = params.slice(0, 160) + "…";
        return (
          "<tr><td class='small text-nowrap'>" +
          fmtTime(row.created_at) +
          "</td><td>" +
          escapeHtml(row.project || "—") +
          "</td><td><code>" +
          escapeHtml(row.method || "") +
          "</code></td><td class='small text-muted'><code>" +
          escapeHtml(params) +
          "</code></td><td class='text-end'>" +
          statusBadge(row) +
          "</td></tr>"
        );
      })
      .join("");
  }

  async function loadAll() {
    try {
      await Promise.all([loadSummary(), loadTimeline(), loadRecent()]);
    } catch (e) {
      console.error("gateway-stats load failed", e);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.getElementById("gw-refresh").addEventListener("click", loadAll);
    document.getElementById("gw-days").addEventListener("change", loadAll);
    let t = null;
    document.getElementById("gw-filter").addEventListener("input", function () {
      clearTimeout(t);
      t = setTimeout(loadRecent, 300);
    });
    loadAll();
  });
})();
