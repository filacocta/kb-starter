/* Hybrid drill-down map (option 3): a global stage strip on top; selecting a
   stage expands a per-topic breakdown below. Per-topic promote edges are
   unambiguous because the topic is known. */
(() => {
  "use strict";
  const { el } = WF;
  const QUEUE_BUCKETS = ["Pending", "Approved", "Auto-approved"];
  let MODEL = null;
  let selected = "Queues";

  const STAGES = [
    { key: "Inbox", kind: "capture", count: (m) => m.capture?.unchecked || 0 },
    { key: "Triage", kind: "triage", count: (m) => m.triage?.unchecked || 0 },
    { key: "Queues", kind: "ladder", count: (m) => QUEUE_BUCKETS.reduce((n, b) => n + m.ladderTotal[b].unchecked, 0) },
    { key: "Ingest", kind: "process", count: (m) => m.ladderTotal["Auto-approved"].unchecked },
    { key: "Synthesis", kind: "output", count: (m) => m.sources },
  ];

  function stageNode(stage) {
    const count = stage.count(MODEL);
    const attn = (stage.key === "Queues" || stage.key === "Inbox" || stage.key === "Ingest") && count > 0;
    const cls = ["wf-stage", attn ? "attn" : "", selected === stage.key ? "selected" : "", count === 0 ? "empty" : ""]
      .filter(Boolean).join(" ");
    return el("button", { class: cls, type: "button", onclick: () => { selected = stage.key; renderStages(); renderDrill(); } }, [
      el("span", { class: "wf-stage-kind", text: stage.kind }),
      el("div", { class: "wf-stage-head" }, [
        el("span", { class: "wf-stage-name", text: stage.key }),
        el("span", { class: "wf-stage-count", text: String(count) }),
      ]),
    ]);
  }

  function renderStages() {
    const host = document.getElementById("stages");
    host.replaceChildren();
    STAGES.forEach((s, i) => {
      host.append(stageNode(s));
      if (i < STAGES.length - 1) host.append(el("span", { class: "wf-edge", "aria-hidden": "true" }, [el("span", { class: "wf-edge-arrow", text: "→" })]));
    });
  }

  async function fetchTopicBucket(t, bucket) {
    const items = await WF.fetchItems(t.queue.path);
    return items.filter((it) => it.ladder === bucket && it.status === "unchecked").map((it) => ({ ...it, topicTitle: t.title || t.slug, path: t.queue.path }));
  }

  function miniNode(t, bucket) {
    const c = (t.queue.ladder?.[bucket]?.unchecked) || 0;
    const node = el("button", {
      class: `wf-mini-node${c > 0 && bucket !== "Approved" ? " attn" : ""}`, type: "button",
      title: `${t.title || t.slug} · ${bucket}: ${c}`,
      onclick: async () => {
        const items = await fetchTopicBucket(t, bucket);
        WF.openDrawer(`${t.title || t.slug} · ${bucket}`, t.queue.path,
          WF.bucketDrawerBody(bucket, [{ slug: t.slug, title: t.title, path: t.queue.path, unchecked: c }], items));
      },
    }, [el("span", { text: bucket }), el("b", { text: String(c) })]);
    return node;
  }

  function miniEdge(t, from, to) {
    const c = (t.queue.ladder?.[from]?.unchecked) || 0;
    return el("button", {
      class: "wf-mini-edge", type: "button", disabled: c === 0 ? "" : null,
      title: c ? `Promote ${c} from ${from} → ${to}` : `Nothing to promote`,
      onclick: c === 0 ? null : async () => {
        const items = await fetchTopicBucket(t, from);
        WF.openDrawer(`Promote: ${t.title || t.slug} · ${from} → ${to}`, t.queue.path,
          WF.bucketDrawerBody(from, [{ slug: t.slug, title: t.title, path: t.queue.path, unchecked: c }], items));
      },
    }, "›");
  }

  function topicLadderRow(t) {
    const mini = el("div", { class: "wf-mini" }, [
      miniNode(t, "Pending"), miniEdge(t, "Pending", "Approved"),
      miniNode(t, "Approved"), miniEdge(t, "Approved", "Auto-approved"),
      miniNode(t, "Auto-approved"),
    ]);
    return el("div", { class: "wf-topic-row" }, [
      el("div", { class: "wf-topic-name" }, [t.title || t.slug, el("span", { class: "hint", text: t.queue.path })]),
      mini,
    ]);
  }

  function renderDrill() {
    const host = document.getElementById("drill");
    host.replaceChildren();
    host.append(el("div", { class: "section-title" }, [el("h2", { text: `${selected} — per topic` })]));

    if (selected === "Queues") {
      const rows = MODEL.topics.filter((t) => QUEUE_BUCKETS.some((b) => (t.queue.ladder?.[b]?.unchecked || 0) > 0));
      if (!rows.length) return host.append(el("div", { class: "wf-drill-empty", text: "No topics with queued items." }));
      rows.forEach((t) => host.append(topicLadderRow(t)));
      return;
    }
    if (selected === "Ingest") {
      const rows = MODEL.topics.filter((t) => (t.queue.ladder?.["Auto-approved"]?.unchecked || 0) > 0);
      host.append(el("p", { class: "hint", text: "Topics with auto-approved items ready for unattended ingestion (/kb-auto)." }));
      if (!rows.length) return host.append(el("div", { class: "wf-drill-empty", text: "Nothing auto-approved is waiting." }));
      rows.forEach((t) => {
        const n = t.queue.ladder["Auto-approved"].unchecked;
        host.append(el("div", { class: "wf-topic-row" }, [
          el("div", { class: "wf-topic-name" }, [t.title || t.slug, el("span", { class: "hint", text: `${n} ready` })]),
          el("div", { class: "wf-mini" }, [miniNode(t, "Auto-approved")]),
        ]));
      });
      return;
    }
    if (selected === "Inbox" || selected === "Triage") {
      const q = selected === "Inbox" ? MODEL.capture : MODEL.triage;
      host.append(el("p", { class: "hint", text: q?.path || "" }));
      openListInline(host, q);
      return;
    }
    if (selected === "Synthesis") {
      const rows = MODEL.topics.filter((t) => (t.sources || 0) + (t.notes || 0) > 0)
        .sort((a, b) => (b.sources || 0) - (a.sources || 0));
      rows.forEach((t) => host.append(el("div", { class: "wf-topic-row" }, [
        el("div", { class: "wf-topic-name", text: t.title || t.slug }),
        el("div", { class: "wf-mini" }, [
          el("span", { class: "wf-mini-node", html: `sources <b>${t.sources || 0}</b>` }),
          el("span", { class: "wf-mini-node", html: `notes <b>${t.notes || 0}</b>` }),
        ]),
      ])));
      return;
    }
  }

  async function openListInline(host, q) {
    if (!q) return host.append(el("div", { class: "wf-drill-empty", text: "Unavailable." }));
    let items = [];
    try { items = (await WF.fetchItems(q.path)).filter((i) => i.status === "unchecked"); } catch {}
    if (!items.length) return host.append(el("div", { class: "wf-drill-empty", text: "Empty." }));
    const list = el("div", { class: "detail-list" });
    items.forEach((it) => list.append(el("div", { class: "detail-item" }, [
      el("div", { class: "detail-meta" }, [el("span", { text: `line ${it.line}` })]),
      el("div", { text: it.text }),
    ])));
    host.append(list);
  }

  function setFreshness(status) {
    const sub = document.getElementById("subhead");
    if (!sub || !status.generated_at) return;
    const t = new Date(status.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    sub.textContent = `Global stages on top; click a stage to drill in · as of ${t}`;
  }

  async function main() {
    WF.initTheme();
    WF.wireDrawer();
    WF.setMutateHandler(main);
    try {
      const status = await WF.fetchStatus();
      MODEL = WF.buildModel(status);
      WF.renderSummary(MODEL);
      setFreshness(status);
      renderStages();
      renderDrill();
    } catch (err) {
      document.getElementById("stages").replaceChildren(el("p", { class: "muted", text: `Failed to load: ${err.message}` }));
    }
  }
  document.getElementById("refresh")?.addEventListener("click", main);
  main();
})();
