/* Global pipeline map (option 1): one left-to-right flow, counts summed
   across all topics. Promote edges are topic-ambiguous, so clicking one
   lists every topic with items in the source bucket. */
(() => {
  "use strict";
  const { el } = WF;

  function stageNode({ kind, name, count, attn, bad, empty, lines, more, onClick }) {
    const cls = ["wf-stage", attn ? "attn" : "", bad ? "bad" : "", empty ? "empty" : ""]
      .filter(Boolean).join(" ");
    const children = [
      el("span", { class: "wf-stage-kind", text: kind }),
      el("div", { class: "wf-stage-head" }, [
        el("span", { class: "wf-stage-name", text: name }),
        el("span", { class: "wf-stage-count", text: String(count) }),
      ]),
    ];
    if (lines && lines.length) {
      children.push(el("div", { class: "wf-stage-list" }, lines.map((t) => el("div", { class: "wf-stage-line", text: t }))));
    }
    if (more > 0) children.push(el("div", { class: "wf-stage-more", text: `+${more} more →` }));
    return el("button", { class: cls, type: "button", onclick: onClick }, children);
  }

  function edgeNode({ label, arrow = "→", move, disabled, onClick }) {
    return el("button", {
      class: `wf-edge${move ? " move" : ""}`, type: "button", disabled: disabled ? "" : null,
      title: label, onclick: disabled ? null : onClick,
    }, [
      el("span", { class: "wf-edge-arrow", text: arrow }),
      el("span", { class: "wf-edge-label", text: label }),
    ]);
  }

  function sampleLines(items, limit = 3) {
    return items.slice(0, limit).map((it) => it.text);
  }

  // Process-edge drawer: explain the step + show the skill/tool to run.
  function processDrawer(title, desc, command) {
    const nodes = [el("p", { class: "hint", text: desc })];
    if (command) {
      nodes.push(el("code", { class: "wf-cmd", text: command }));
      nodes.push(el("div", { class: "wf-action-row" }, [
        el("button", { type: "button", text: "Copy command", onclick: () => WF.copy(command) }),
      ]));
    }
    WF.openDrawer(title, "process step", nodes);
  }

  function render(model, index) {
    const flow = document.getElementById("flow");
    flow.replaceChildren();
    const lt = model.ladderTotal;
    const captureItems = (model.capture?.preview_items || []).filter((i) => i.status === "unchecked");
    const triageItems = (model.triage?.preview_items || []).filter((i) => i.status === "unchecked");
    const pending = index["Pending"].filter((i) => i.status === "unchecked");
    const approved = index["Approved"].filter((i) => i.status === "unchecked");
    const auto = index["Auto-approved"].filter((i) => i.status === "unchecked");
    const failed = index["Failed — Retry"];

    const captureN = model.capture?.unchecked || 0;
    const triageN = model.triage?.unchecked || 0;

    const seq = [];
    // Capture
    seq.push(stageNode({
      kind: "capture", name: "URL Inbox", count: captureN, attn: captureN > 0,
      lines: sampleLines(captureItems), more: Math.max(0, captureN - captureItems.length),
      onClick: () => openInbox("URL Inbox", model.capture?.path, "Captured URLs awaiting routing to a topic."),
    }));
    seq.push(edgeNode({
      label: "route", onClick: () => processDrawer("Route to a topic",
        "Triage captured URLs into a topic's reading list. Run a topic-scoped triage / capture-marker session.",
        "# set the topic, then capture/triage routes here\necho rockhounding > ~/.local/state/kb-feed/capture-topic"),
    }));
    // Triage
    seq.push(stageNode({
      kind: "triage", name: "Topic Interests", count: triageN, attn: triageN > 0,
      lines: sampleLines(triageItems), more: Math.max(0, triageN - triageItems.length),
      onClick: () => openInbox("Topic Interests", model.triage?.path, "Broad topic interests awaiting source discovery."),
    }));
    seq.push(edgeNode({
      label: "assign", onClick: () => processDrawer("Assign / discover sources",
        "Turn an interest into concrete queued URLs under a topic's ## Pending.", null),
    }));
    // Pending
    seq.push(ladderStage("queue", "Pending", lt.Pending.unchecked, pending, model.byBucket.Pending));
    seq.push(promoteEdge("Pending", "Approved", pending));
    // Approved
    seq.push(ladderStage("queue", "Approved", lt.Approved.unchecked, approved, model.byBucket.Approved));
    seq.push(promoteEdge("Approved", "Auto-approved", approved));
    // Auto-approved
    seq.push(ladderStage("queue", "Auto-approved", lt["Auto-approved"].unchecked, auto, model.byBucket["Auto-approved"]));
    seq.push(edgeNode({
      label: "ingest", onClick: () => processDrawer("Ingest",
        "Fetch auto-approved items and write source notes. Unattended run via kb-auto, or interactive via kb-queue.",
        "/kb-auto      # unattended, processes ## Auto-approved\n# or: /kb-queue <topic>   # interactive batch"),
    }));
    // Processed
    seq.push(stageNode({
      kind: "done", name: "Processed", count: lt.Processed.checked,
      lines: [], onClick: () => WF.openDrawer("Processed", "ingested items across topics",
        [el("p", { class: "muted", text: `${lt.Processed.checked} items marked [x] across all topic ladders.` })]),
    }));
    seq.push(edgeNode({
      label: "synthesise", onClick: () => processDrawer("Synthesise",
        "Turn source notes into concept/wiki notes once a topic has ≥2 sources.", "/kb-synthesise <topic>"),
    }));
    // Sources / Wiki
    seq.push(stageNode({
      kind: "output", name: "Sources + Wiki", count: model.sources,
      lines: [`${model.sources} source notes`, `${model.notes} wiki/concept notes`], more: 0,
      onClick: () => WF.openDrawer("Outputs", "knowledge written to the vault",
        [el("p", { class: "muted", text: `${model.sources} source notes and ${model.notes} synthesised notes across ${model.topics.length} topics.` })]),
    }));

    seq.forEach((n) => flow.append(n));

    // Failed — Retry hangs below as its own node when non-empty.
    if (failed.length) {
      const frow = el("div", { class: "wf-flow wf-row-break" }, [
        stageNode({
          kind: "exception", name: "Failed — Retry", count: failed.length, bad: true,
          lines: sampleLines(failed), more: Math.max(0, failed.length - 3),
          onClick: () => WF.openDrawer("Failed — Retry", "items needing a retry",
            WF.bucketDrawerBody("Failed — Retry", model.byBucket["Failed — Retry"], failed)),
        }),
      ]);
      flow.append(frow);
    }

    function ladderStage(kind, bucket, count, items, entries) {
      return stageNode({
        kind, name: bucket, count, attn: count > 0, empty: count === 0,
        lines: sampleLines(items), more: Math.max(0, count - Math.min(3, items.length)),
        onClick: () => WF.openDrawer(bucket, `${count} item(s) across ${entries.length} topic(s)`,
          WF.bucketDrawerBody(bucket, entries, items)),
      });
    }
    function promoteEdge(from, to, items) {
      const n = items.length;
      return edgeNode({
        label: n ? `move ${n}` : "move", move: true, disabled: n === 0,
        onClick: () => WF.openDrawer(`Promote: ${from} → ${to}`,
          `${n} item(s) eligible across topics — pick per item`,
          WF.bucketDrawerBody(from, model.byBucket[from], items)),
      });
    }
  }

  async function openInbox(title, path, desc) {
    let items = [];
    try { items = (await WF.fetchItems(path)).filter((i) => i.status === "unchecked"); } catch {}
    const nodes = [el("p", { class: "hint", text: desc })];
    if (items.length) {
      const list = el("div", { class: "detail-list" });
      for (const it of items) list.append(el("div", { class: "detail-item" }, [
        el("div", { class: "detail-meta" }, [el("span", { text: `line ${it.line}` })]),
        el("div", { text: it.text }),
      ]));
      nodes.push(list);
    } else nodes.push(el("p", { class: "muted", text: "Empty." }));
    WF.openDrawer(title, path || "", nodes);
  }

  async function loadIndex(model) {
    const index = {};
    WF.LADDER.forEach((n) => (index[n] = []));
    const jobs = model.topics
      .filter((t) => (t.queue.unchecked || 0) + (t.queue.failed || 0) > 0)
      .map(async (t) => {
        const items = await WF.fetchItems(t.queue.path);
        for (const it of items) {
          if (index[it.ladder]) index[it.ladder].push({ ...it, topicTitle: t.title || t.slug, path: t.queue.path });
        }
      });
    await Promise.all(jobs);
    return index;
  }

  function setFreshness(status) {
    const sub = document.getElementById("subhead");
    if (!sub || !status.generated_at) return;
    const t = new Date(status.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    sub.textContent = `One pipeline, counts summed across all topics · as of ${t}`;
  }

  async function main() {
    WF.initTheme();
    WF.wireDrawer();
    WF.setMutateHandler(main);
    try {
      const status = await WF.fetchStatus();
      const model = WF.buildModel(status);
      const index = await loadIndex(model);
      WF.renderSummary(model);
      setFreshness(status);
      render(model, index);
    } catch (err) {
      document.getElementById("flow").replaceChildren(el("p", { class: "muted", text: `Failed to load: ${err.message}` }));
    }
  }
  document.getElementById("refresh")?.addEventListener("click", main);
  main();
})();
