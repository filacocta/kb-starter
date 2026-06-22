/* Shared helpers for the workflow-map prototypes. Read-only: the only
   "write" surface is generating copyable handoff commands. No POST calls. */
const WF = (() => {
  "use strict";

  const LADDER = ["Pending", "Approved", "Auto-approved", "Processed", "Failed — Retry"];
  // Promotion edges that are pure section-moves within a topic's reading-list.
  const PROMOTE = { Pending: "Approved", Approved: "Auto-approved" };
  const themeKey = "kb-dashboard-theme";

  /* ---- theme (shared with the main dashboard) ---- */
  function initTheme() {
    const stored = localStorage.getItem(themeKey);
    const theme = stored === "dark" || stored === "light" ? stored : "light";
    applyTheme(theme);
    const btn = document.getElementById("theme-toggle");
    btn?.addEventListener("click", () =>
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")
    );
  }
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(themeKey, theme);
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = theme === "dark" ? "Light" : "Dark";
  }

  /* ---- tiny DOM + feedback helpers ---- */
  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) node.setAttribute(k, v);
    }
    for (const child of [].concat(children)) {
      if (child == null) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  let toastTimer = null;
  function toast(msg) {
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
  }
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied to clipboard");
    } catch {
      toast("Copy failed — select manually");
    }
  }

  /* ---- data ---- */
  async function fetchStatus() {
    const r = await fetch("/api/status", { cache: "no-store" });
    if (!r.ok) throw new Error(`status ${r.status}`);
    return r.json();
  }
  async function postJSON(path, body) {
    try {
      const r = await fetch(path, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      return await r.json();
    } catch (err) {
      return { ok: false, error: "request_failed", message: String(err) };
    }
  }

  // Re-render hook so a successful mutation refreshes the underlying map counts.
  let mutateHandler = null;
  function setMutateHandler(fn) { mutateHandler = fn; }

  // Live promote: preview -> confirm -> execute, against the guarded endpoints.
  async function livePromote(item, from, to, cardEl) {
    const body = { file: item.path, url: itemKey(item.text), from, to };
    const pv = await postJSON("/api/promote/preview", body);
    if (!pv.ok) { toast(`Preview failed: ${pv.message || pv.error || "error"}`); return; }
    if (!window.confirm(`Promote ${from} → ${to}?\n\n${pv.moved_text || item.text}`)) return;
    const ex = await postJSON("/api/promote/execute", body);
    if (!ex.ok) { toast(`Promote failed: ${ex.message || ex.error || "error"}`); return; }
    toast(`Promoted → ${to}`);
    if (cardEl) cardEl.remove();
    if (mutateHandler) mutateHandler();
  }
  async function fetchItems(path) {
    const r = await fetch(`/api/queue?path=${encodeURIComponent(path)}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`queue ${r.status}`);
    const data = await r.json();
    return data.items || [];
  }

  /* ---- model: aggregate the per-topic ladders into a global picture ---- */
  function buildModel(status) {
    const topics = (status.topics || []).filter((t) => t.queue);
    const ladderTotal = {};
    for (const name of LADDER) ladderTotal[name] = { unchecked: 0, checked: 0, failed: 0 };
    const byBucket = {}; // bucket -> [{slug,title,path,unchecked}]
    for (const name of LADDER) byBucket[name] = [];
    for (const t of topics) {
      const lad = t.queue.ladder || {};
      for (const name of LADDER) {
        const c = lad[name] || { unchecked: 0, checked: 0, failed: 0 };
        ladderTotal[name].unchecked += c.unchecked || 0;
        ladderTotal[name].checked += c.checked || 0;
        ladderTotal[name].failed += c.failed || 0;
        if ((c.unchecked || 0) > 0)
          byBucket[name].push({ slug: t.slug, title: t.title || t.slug, path: t.queue.path, unchecked: c.unchecked });
      }
    }
    const sources = topics.reduce((n, t) => n + (t.sources || 0), 0);
    const notes = topics.reduce((n, t) => n + (t.notes || 0), 0);
    const capture = status.global_queues?.[0] || null; // Links inbox
    const triage = status.global_queues?.[1] || null; // topics inbox
    return { status, topics, ladderTotal, byBucket, sources, notes, capture, triage };
  }

  // Identifying substring for kb-checkoff/kb-promote: the leading token (URL).
  function itemKey(text) {
    return (text || "").trim().split(/\s+/)[0] || "";
  }

  function promoteCommand(relPath, itemText, from, to) {
    const key = itemKey(itemText).replace(/"/g, '\\"');
    return (
      `python3 _tools/kb-promote.py \\\n` +
      `  --file ${relPath} \\\n` +
      `  --url "${key}" \\\n` +
      `  --from "${from}" --to "${to}"`
    );
  }

  /* ---- drawer (mirrors the main dashboard drawer behaviour) ---- */
  let lastFocus = null;
  function openDrawer(title, subtitle, contentNodes) {
    const drawer = document.getElementById("detail-drawer");
    document.getElementById("drawer-title").textContent = title;
    document.getElementById("drawer-subtitle").textContent = subtitle || "";
    const body = document.getElementById("drawer-content");
    body.replaceChildren(...[].concat(contentNodes).filter(Boolean));
    lastFocus = document.activeElement;
    drawer.classList.remove("hidden");
    drawer.setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");
    document.getElementById("drawer-close").focus();
  }
  function closeDrawer() {
    const drawer = document.getElementById("detail-drawer");
    drawer.classList.add("hidden");
    drawer.setAttribute("aria-hidden", "true");
    document.body.classList.remove("drawer-open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function wireDrawer() {
    document.getElementById("drawer-close")?.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !document.getElementById("detail-drawer").classList.contains("hidden"))
        closeDrawer();
    });
  }

  /* "What needs attention now" — the lead decision for both maps. */
  function attentionSummary(model) {
    const sum = (b) => model.byBucket[b].reduce((n, e) => n + e.unchecked, 0);
    return {
      route: (model.capture?.unchecked || 0) + (model.triage?.unchecked || 0),
      promote: sum("Pending") + sum("Approved"),
      ingest: model.ladderTotal["Auto-approved"].unchecked,
      failed: model.ladderTotal["Failed — Retry"].unchecked + model.ladderTotal["Failed — Retry"].failed,
    };
  }
  function renderSummary(model, hostId = "wf-summary") {
    const host = document.getElementById(hostId);
    if (!host) return;
    const s = attentionSummary(model);
    const parts = [];
    if (s.route) parts.push(["act", s.route, "to route"]);
    if (s.promote) parts.push(["act", s.promote, "to promote"]);
    if (s.ingest) parts.push(["act", s.ingest, "ready to ingest"]);
    if (s.failed) parts.push(["bad", s.failed, s.failed === 1 ? "failed" : "failed"]);
    host.replaceChildren();
    if (!parts.length) {
      host.append(el("div", { class: "wf-sum-item clear" }, [el("span", { class: "wf-sum-label", text: "Pipeline clear — nothing waiting" })]));
      return;
    }
    for (const [cls, num, label] of parts) {
      host.append(el("div", { class: `wf-sum-item ${cls}` }, [
        el("span", { class: "wf-sum-num", text: String(num) }),
        el("span", { class: "wf-sum-label", text: label }),
      ]));
    }
  }

  // Build a drawer body for a ladder bucket: move commands grouped by topic.
  function bucketDrawerBody(bucketName, entries, items) {
    const nodes = [];
    const to = PROMOTE[bucketName];
    if (to) {
      nodes.push(el("p", { class: "hint", text: `Promote edge: ${bucketName} → ${to}. Preview of the move contract, grouped by topic:` }));
    }
    if (!items || !items.length) {
      nodes.push(el("p", { class: "muted", text: "No items in this section." }));
      return nodes;
    }
    const groups = new Map();
    for (const it of items) {
      const key = it.path || it.topicTitle || "";
      if (!groups.has(key)) groups.set(key, { title: it.topicTitle || key, path: it.path, items: [] });
      groups.get(key).items.push(it);
    }
    const many = groups.size > 1;
    for (const g of groups.values()) {
      const body = el("div", { class: "wf-group-body" });
      const commands = [];
      for (const it of g.items) {
        const card = el("div", { class: "detail-item" }, [
          el("div", { class: "detail-meta" }, [el("span", { text: `line ${it.line}` })]),
          el("div", { text: it.text }),
        ]);
        if (to) {
          const cmd = promoteCommand(it.path, it.text, bucketName, to);
          commands.push(cmd);
          card.append(el("div", { class: "wf-action-row" }, [
            el("button", { type: "button", class: "wf-go", text: `Promote ▸ ${to}`, onclick: () => livePromote(it, bucketName, to, card) }),
            el("button", { type: "button", text: "Copy command", onclick: () => copy(cmd) }),
          ]));
          card.append(el("code", { class: "wf-cmd", text: cmd }));
        }
        body.append(card);
      }
      const summary = el("summary", {}, [
        `${g.title} `,
        el("span", { class: "hint", text: `· ${g.items.length} item(s)` }),
      ]);
      const det = el("details", { class: "wf-group" }, [summary, body]);
      if (!many) det.open = true;
      if (to && commands.length > 1) {
        body.prepend(el("div", { class: "wf-action-row" }, [
          el("button", { type: "button", text: `Copy all ${commands.length} for this topic`, onclick: () => copy(commands.join("\n\n")) }),
        ]));
      }
      nodes.push(det);
    }
    return nodes;
  }

  return {
    LADDER, PROMOTE,
    initTheme, el, toast, copy,
    fetchStatus, fetchItems, postJSON, buildModel, itemKey, promoteCommand,
    attentionSummary, renderSummary, setMutateHandler, livePromote,
    openDrawer, closeDrawer, wireDrawer, bucketDrawerBody,
  };
})();
