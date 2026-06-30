"use strict";
// Project Health page — renders /api/project-health (per-project operational metrics written by
// external jobs, e.g. the sportsbook dismissal review).

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const fmtAge = (iso) => {
  if (!iso) return "";
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const h = ms / 3.6e6;
  if (h < 1) return `${Math.round(ms / 6e4)}m ago`;
  if (h < 48) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
};

async function load() {
  let data;
  try {
    const r = await fetch("/api/project-health", { cache: "no-store" });
    data = await r.json();
  } catch (e) {
    $("#subhead").textContent = "Failed to load /api/project-health";
    return;
  }
  const s = data.summary || {};
  const total = (data.projects || []).reduce((a, p) => a + (p.metrics || []).length, 0);
  $("#subhead").textContent =
    `${(data.projects || []).length} project(s) · ${total} metric(s)` +
    (s.stale ? ` · ${s.stale} stale` : "");

  const sum = $("#ph-summary");
  sum.innerHTML = "";
  for (const [k, label] of [["red", "needs attention"], ["yellow", "watch"], ["green", "ok"], ["stale", "stale"]]) {
    if (s[k]) sum.appendChild(el("span", `ph-pill ${k}`, `${s[k]} ${label}`));
  }
  if (!s.red && !s.yellow && !s.green && !s.stale) {
    sum.appendChild(el("span", "ph-summary-text",
      `No metrics yet — drop <project>.json files in ${data.health_dir || "the health dir"}.`));
  }

  const wrap = $("#ph-projects");
  wrap.innerHTML = "";
  if (!(data.projects || []).length) {
    wrap.appendChild(el("p", "ph-empty", "No project-health metrics reported yet."));
    return;
  }
  for (const p of data.projects) {
    const card = el("article", "panel ph-project");
    const h = el("h2");
    h.appendChild(el("span", `ph-dot ${p.worst || "green"}`));
    h.appendChild(document.createTextNode(p.title || p.project || "project"));
    if (p.updated) {
      const u = el("span", "ph-updated", ` · ${fmtAge(p.updated)}`);
      h.appendChild(u);
    }
    card.appendChild(h);
    for (const m of (p.metrics || [])) {
      const row = el("div", "ph-metric");
      const head = el("div", "ph-metric-head");
      head.appendChild(el("span", "ph-metric-name", m.name || "metric"));
      head.appendChild(el("span", `ph-badge ${m.status || "green"}`, m.stale ? "stale" : (m.status || "green")));
      if (m.summary) head.appendChild(el("span", "ph-summary-text", m.summary));
      if (m.updated) head.appendChild(el("span", "ph-updated", fmtAge(m.updated)));
      row.appendChild(head);
      if (m.detail) row.appendChild(el("pre", "ph-detail", m.detail));
      card.appendChild(row);
    }
    wrap.appendChild(card);
  }
}

$("#refresh").addEventListener("click", load);
load();
setInterval(load, 60000);
