const state = {
  data: null,
  topicFilter: "",
  topicMode: "all",
  topicSort: { key: "queued", dir: "desc" },
  projectSort: { key: "open", dir: "desc" },
  activeQueuePath: "",
  previousFocus: null,
  toastTimer: null,
};

const queueCache = new Map();
const $ = (id) => document.getElementById(id);
const themeKey = "kb-dashboard-theme";
const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function preferredTheme() {
  const stored = localStorage.getItem(themeKey);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(themeKey, theme);
  const button = $("theme-toggle");
  if (!button) return;
  const next = theme === "dark" ? "Light" : "Dark";
  button.textContent = next;
  button.setAttribute("aria-label", `Switch to ${next.toLowerCase()} theme`);
  button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

function sum(items, getter) {
  return items.reduce((total, item) => total + getter(item), 0);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function pill(text, kind = "") {
  return `<span class="status-pill ${kind}">${escapeHtml(text || "unknown")}</span>`;
}

function metric(label, value, detail = "") {
  return `
    <div class="metric">
      <span class="value">${escapeHtml(value)}</span>
      <span class="label">${escapeHtml(label)}</span>
      ${detail ? `<span class="hint">${escapeHtml(detail)}</span>` : ""}
    </div>
  `;
}

function queueItems(queue) {
  return queue.items || queue.preview_items || [];
}

function queueAttentionItems(queue) {
  return queueItems(queue).filter((item) => ["unchecked", "failed", "active", "review"].includes(item.status));
}

function kindLabel(queue) {
  return {
    "generated": "Generated read-only view",
    "parking-lot": "Ideas parking lot",
    "queue": "Workflow queue",
  }[queue.kind || "queue"];
}

function queueCard(queue) {
  const active = state.activeQueuePath === queue.path ? "selected" : "";
  const attention = queueAttentionItems(queue).slice(0, 3);
  const sections = queue.sections
    .filter((section) => section.unchecked || section.failed || section.active)
    .slice(0, 3)
    .map((section) => `<span class="hint">${escapeHtml(section.heading)}: ${section.unchecked} open, ${section.active} active, ${section.failed} failed</span>`)
    .join("");
  return `
    <button class="queue-card ${active}" type="button" data-queue-path="${escapeHtml(queue.path)}" aria-label="Open ${escapeHtml(queue.name)} details">
      <span class="queue-title">${escapeHtml(queue.name)}</span>
      <span class="kind-label">${escapeHtml(kindLabel(queue))}</span>
      <span class="hint">${escapeHtml(queue.path)}</span>
      <span class="queue-counts">
        <span class="count-chip warn">${queue.unchecked} open</span>
        <span class="count-chip">${queue.checked} done</span>
        <span class="count-chip">${queue.active || 0} active</span>
        <span class="count-chip ${queue.review ? "warn" : ""}">${queue.review || 0} review</span>
        <span class="count-chip ${queue.failed ? "bad" : ""}">${queue.failed} failed</span>
      </span>
      ${sections ? `<span class="list compact">${sections}</span>` : ""}
      ${attention.length ? `<span class="item-preview">${attention.map((item) => escapeHtml(item.text)).join("<br>")}</span>` : ""}
    </button>
  `;
}

function renderMetrics(data) {
  const topicOpen = sum(data.topics, (topic) => topic.queue ? topic.queue.unchecked : 0);
  const topicFailed = sum(data.topics, (topic) => topic.queue ? topic.queue.failed : 0);
  const projectOpen = sum(data.projects, (project) => project.todo_counts?.unchecked || 0);
  const globalOpen = sum(data.global_queues, (queue) => queue.unchecked);
  const ideasOpen = data.ideas_backlog?.unchecked || 0;
  const activeProjectTodos = sum(data.projects, (project) => project.todo_counts?.active || 0);
  const reviewProjectTodos = sum(data.projects, (project) => project.todo_counts?.review || 0);
  const topicsWithOpen = data.topics.filter((topic) => (topic.queue?.unchecked || 0) > 0).length;
  const projectsWithOpen = data.projects.filter((project) => (project.todo_counts?.unchecked || 0) > 0).length;
  const statusSummary = [
    topicFailed ? `${topicFailed} failed queue item${topicFailed === 1 ? "" : "s"}` : "",
    activeProjectTodos ? `${activeProjectTodos} active project todo${activeProjectTodos === 1 ? "" : "s"}` : "",
    data.checkpoint?.is_active ? "active checkpoint" : "checkpoint complete",
  ].filter(Boolean).join(" | ");
  $("status-summary").textContent = statusSummary || "No urgent status flags in aggregate counts.";
  $("metrics").innerHTML = [
    metric("Topics", data.topics.length, topicsWithOpen ? `${topicsWithOpen} with open queues` : "all clear"),
    metric("Projects", data.projects.length, projectsWithOpen ? `${projectsWithOpen} with open todos` : "all clear"),
    metric("Topic queue", topicOpen, topicFailed ? `${topicFailed} failed` : "open source work"),
    metric("Global queue", globalOpen, "inbox + topic interests"),
    metric("Ideas", ideasOpen, data.ideas_backlog?.active ? `${data.ideas_backlog.active} active` : "parked"),
    metric("Project todos", projectOpen, [activeProjectTodos ? `${activeProjectTodos} active` : "", reviewProjectTodos ? `${reviewProjectTodos} review` : ""].filter(Boolean).join(", ") || "open"),
    metric("Dive queue", data.dive_queue.unchecked, "generated view"),
  ].join("");
}

function plural(count, singular, pluralWord = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

function queuePressure(queue) {
  return (queue.failed || 0) * 10 + (queue.active || 0) * 4 + (queue.review || 0) * 3 + (queue.unchecked || 0);
}

function dayAge(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.floor((Date.now() - date.getTime()) / 86400000);
}

function feedRowsNeedingAttention(rows = []) {
  const quiet = new Set(["written", "sent", "done", "complete", "completed", "ok", "success", "succeeded"]);
  return rows.filter((row) => Number(row.count || 0) > 0 && !quiet.has(String(row.status || "").toLowerCase()));
}

function attentionAction(item) {
  if (item.queuePath) {
    return {
      label: "Inspect queue",
      hint: "opens drawer",
      kind: "inspect",
      aria: `Inspect queue details for ${item.title}`,
    };
  }
  if (item.path) {
    return {
      label: "Copy path",
      hint: "clipboard",
      kind: "copy",
      aria: `Copy source path for ${item.title}`,
    };
  }
  return {
    label: item.action || "Read status",
    hint: "read-only",
    kind: "status",
    aria: item.title || "Read dashboard status",
  };
}

function attentionCard(item) {
  const action = attentionAction(item);
  const body = `
    <span class="attention-meta">
      <span class="attention-level ${escapeHtml(item.level)}">${escapeHtml(item.label)}</span>
      ${item.path ? `<span class="attention-path">${escapeHtml(item.path)}</span>` : ""}
    </span>
    <span class="attention-main">
      <span class="attention-title">${escapeHtml(item.title)}</span>
      <span class="attention-detail">${escapeHtml(item.detail)}</span>
    </span>
    <span class="attention-action ${escapeHtml(action.kind)}">
      <span class="action-label">${escapeHtml(action.label)}</span>
      <span class="action-hint">${escapeHtml(action.hint)}</span>
    </span>
  `;
  if (item.queuePath) {
    return `<button class="attention-card ${escapeHtml(item.level)}" type="button" data-queue-path="${escapeHtml(item.queuePath)}" aria-label="${escapeHtml(action.aria)}">${body}</button>`;
  }
  if (item.path) {
    return `<button class="attention-card ${escapeHtml(item.level)}" type="button" data-copy="${escapeHtml(item.path)}" aria-label="${escapeHtml(action.aria)}">${body}</button>`;
  }
  return `<div class="attention-card ${escapeHtml(item.level)}">${body}</div>`;
}

function buildAttentionItems(data) {
  const items = [];
  const add = (item) => items.push(item);

  if (data.checkpoint?.is_active) {
    add({
      priority: 100,
      level: "bad",
      label: "checkpoint",
      title: "Resume or resolve the active checkpoint",
      detail: data.checkpoint.operation || "A KB operation is marked active or resumable.",
      action: "Open checkpoint",
      path: data.checkpoint.path,
    });
  }

  const queues = allQueues(data).filter(Boolean);
  queues
    .filter((queue) => queue.failed > 0)
    .sort((a, b) => b.failed - a.failed || queuePressure(b) - queuePressure(a))
    .slice(0, 5)
    .forEach((queue) => add({
      priority: 92,
      level: "bad",
      label: "failed",
      title: `${queue.name} has ${plural(queue.failed, "failed item")}`,
      detail: "Open the queue detail drawer to inspect the source line and failure context.",
      action: "Open queue",
      path: queue.path,
      queuePath: queue.path,
    }));

  queues
    .filter((queue) => queue.active > 0)
    .sort((a, b) => b.active - a.active || queuePressure(b) - queuePressure(a))
    .slice(0, 5)
    .forEach((queue) => add({
      priority: 84,
      level: "warn",
      label: "active",
      title: `${queue.name} has ${plural(queue.active, "active item")}`,
      detail: "These are already in motion and should be finished, paused, or marked clearly.",
      action: "Resolve state",
      path: queue.path,
      queuePath: queue.path,
    }));

  queues
    .filter((queue) => (queue.review || 0) > 0)
    .sort((a, b) => (b.review || 0) - (a.review || 0) || queuePressure(b) - queuePressure(a))
    .slice(0, 5)
    .forEach((queue) => add({
      priority: 78,
      level: "warn",
      label: "review",
      title: `${queue.name} has ${plural(queue.review || 0, "review item")}`,
      detail: "These are marked for testing or review before they can be considered done.",
      action: "Review item",
      path: queue.path,
      queuePath: queue.path,
    }));

  const feedOutbox = feedRowsNeedingAttention(data.kb_feed?.outbox);
  const feedCapture = feedRowsNeedingAttention(data.kb_feed?.capture_outbox);
  if (data.kb_feed?.available && (feedOutbox.length || feedCapture.length)) {
    add({
      priority: 82,
      level: "warn",
      label: "kb-feed",
      title: "kb-feed has pending or failed runtime rows",
      detail: [...feedOutbox, ...feedCapture].map((row) => `${row.status}: ${row.count}`).join(", "),
      action: "Inspect state",
      path: data.kb_feed.path,
    });
  } else if (data.kb_feed && !data.kb_feed.available && data.kb_feed.error !== "state DB not found") {
    add({
      priority: 64,
      level: "warn",
      label: "kb-feed",
      title: "kb-feed state could not be read",
      detail: data.kb_feed.error || "State unavailable.",
      action: "Check state",
      path: data.kb_feed.path,
    });
  }

  data.global_queues
    .filter((queue) => queue.unchecked > 0)
    .sort((a, b) => b.unchecked - a.unchecked)
    .forEach((queue) => add({
      priority: 74,
      level: "warn",
      label: "inbox",
      title: `${queue.name} has ${plural(queue.unchecked, "open item")}`,
      detail: "These should be assigned, dropped, or promoted before deeper batch work.",
      action: "Triage inbox",
      path: queue.path,
      queuePath: queue.path,
    }));

  data.topics
    .filter((topic) => (topic.queue?.unchecked || 0) > 0 && !(topic.queue?.failed || 0) && !(topic.queue?.active || 0))
    .sort((a, b) => (b.queue?.unchecked || 0) - (a.queue?.unchecked || 0))
    .slice(0, 6)
    .forEach((topic) => add({
      priority: 62,
      level: "info",
      label: "topic",
      title: `${topic.slug} has ${plural(topic.queue.unchecked, "queued source")}`,
      detail: topic.title || "Topic reading list has open work.",
      action: "Open topic queue",
      path: topic.queue.path,
      queuePath: topic.queue.path,
    }));

  data.projects
    .filter((project) => (project.todo_counts?.failed || 0) > 0 || (project.todo_counts?.active || 0) > 0 || (project.todo_counts?.review || 0) > 0 || (project.todo_counts?.unchecked || 0) > 0)
    .sort((a, b) => queuePressure(b.todo_counts || {}) - queuePressure(a.todo_counts || {}))
    .slice(0, 5)
    .forEach((project) => {
      const counts = project.todo_counts || {};
      const level = counts.failed ? "bad" : counts.active || counts.review ? "warn" : "info";
      const label = counts.failed ? "blocked" : counts.active ? "active" : counts.review ? "review" : "todos";
      add({
        priority: counts.failed ? 88 : counts.active ? 76 : counts.review ? 70 : 58,
        level,
        label,
        title: `${project.slug} has project todo work`,
        detail: `${counts.unchecked || 0} open, ${counts.active || 0} active, ${counts.review || 0} review, ${counts.failed || 0} blocked.`,
        action: counts.failed ? "Unblock" : counts.active ? "Finish active" : counts.review ? "Review" : "Plan next",
        path: `projects/${project.slug}/_todos.md`,
      });
    });

  if (data.ideas_backlog?.unchecked > 0) {
    add({
      priority: 54,
      level: "info",
      label: "ideas",
      title: `${plural(data.ideas_backlog.unchecked, "idea")} parked for later triage`,
      detail: "Review for promotion into a project, topic research idea, exact source queue, or skip note.",
      action: "Triage ideas",
      path: data.ideas_backlog.path,
      queuePath: data.ideas_backlog.path,
    });
  }

  // The dive queue is a generated read-only view (derived from topic ## Auto-approved
  // sections + research-ideas.md). It is intentionally NOT an attention item — you act on
  // the underlying topic queues, not the generated view. It remains in the metrics strip.

  [...data.projects, ...data.topics]
    .map((row) => ({ ...row, age: dayAge(row.modified), kind: row.todo_counts ? "project" : "topic" }))
    .filter((row) => row.age !== null && row.age >= (row.kind === "project" ? 21 : 30))
    .sort((a, b) => b.age - a.age)
    .slice(0, 4)
    .forEach((row) => add({
      priority: 35,
      level: "info",
      label: "stale",
      title: `${row.kind === "project" ? "Project" : "Topic"} ${row.slug} is ${row.age} days stale`,
      detail: row.title || "Summary has not been touched recently.",
      action: "Review summary",
      path: `${row.kind === "project" ? "projects" : "topics"}/${row.slug}/_summary.md`,
    }));

  return items
    .sort((a, b) => b.priority - a.priority || a.title.localeCompare(b.title))
    .slice(0, 12);
}

function renderAttention(data) {
  const items = buildAttentionItems(data);
  const critical = items.filter((item) => item.level === "bad").length;
  const warnings = items.filter((item) => item.level === "warn").length;
  $("attention-summary").textContent = items.length
    ? `${plural(critical, "critical", "critical")} | ${plural(warnings, "warning")} | ${plural(items.length, "item")} shown`
    : "No obvious blockers in the current read-only signals.";
  $("attention-list").innerHTML = items.length
    ? items.map(attentionCard).join("")
    : `<div class="attention-empty">No failed queues, active checkpoints, kb-feed issues, or obvious stale work surfaced.</div>`;
}

function renderCheckpoint(checkpoint) {
  const kind = checkpoint.is_active ? "active" : checkpoint.status === "complete" ? "ok" : "warn";
  $("checkpoint-panel").querySelector(".content").innerHTML = `
    <div class="list">
      <div>${pill(checkpoint.status, kind)}</div>
      <div><strong>${escapeHtml(checkpoint.operation)}</strong></div>
      <div class="hint">${escapeHtml(checkpoint.path)}${checkpoint.last_updated ? ` | ${escapeHtml(checkpoint.last_updated)}` : ""}</div>
      <div class="queue-counts">
        <span class="count-chip">${checkpoint.counts.unchecked} pending</span>
        <span class="count-chip">${checkpoint.counts.active} active</span>
        <span class="count-chip ${checkpoint.counts.review ? "warn" : ""}">${checkpoint.counts.review || 0} review</span>
        <span class="count-chip">${checkpoint.counts.checked} done</span>
        <span class="count-chip ${checkpoint.counts.failed ? "bad" : ""}">${checkpoint.counts.failed} failed</span>
      </div>
    </div>
  `;
}

function renderKbFeed(feed) {
  if (!feed.available) {
    $("kb-feed-panel").querySelector(".content").innerHTML = `<div class="muted">${escapeHtml(feed.error || "State unavailable")}</div>`;
    return;
  }
  const outbox = (feed.outbox || []).map((row) => `<span class="count-chip">${escapeHtml(row.status)}: ${row.count}</span>`).join("");
  const capture = (feed.capture_outbox || []).map((row) => `<span class="count-chip">${escapeHtml(row.status)}: ${row.count}</span>`).join("");
  const meta = feed.runtime_meta || {};
  $("kb-feed-panel").querySelector(".content").innerHTML = `
    <div class="list">
      <button class="path-button" type="button" data-copy="${escapeHtml(feed.path)}">${escapeHtml(feed.path)}</button>
      <div><strong>Outbox</strong><div class="queue-counts">${outbox || '<span class="muted">none</span>'}</div></div>
      <div><strong>Capture</strong><div class="queue-counts">${capture || '<span class="muted">none</span>'}</div></div>
      <div class="hint">Last activity: ${escapeHtml(meta.last_activity_at || "unknown")}</div>
      <div class="hint">Last KB commit: ${escapeHtml(meta.last_kb_commit_at || "unknown")}</div>
    </div>
  `;
}

function allQueues(data) {
  return [...data.global_queues, data.ideas_backlog, data.dive_queue, ...data.topics.map((topic) => topic.queue).filter(Boolean)].filter(Boolean);
}

function findQueue(path) {
  if (!state.data) return null;
  return allQueues(state.data).find((queue) => queue.path === path) || null;
}

function replaceQueue(queue) {
  if (!state.data) return;
  state.data.global_queues = state.data.global_queues.map((item) => item.path === queue.path ? queue : item);
  if (state.data.ideas_backlog?.path === queue.path) state.data.ideas_backlog = queue;
  if (state.data.dive_queue?.path === queue.path) state.data.dive_queue = queue;
  state.data.topics.forEach((topic) => {
    if (topic.queue?.path === queue.path) topic.queue = queue;
  });
}

function renderQueues(data) {
  const queues = [...data.global_queues, data.ideas_backlog, data.dive_queue].filter(Boolean);
  if (!state.activeQueuePath && queues.length) state.activeQueuePath = queues[0].path;
  $("global-queues").innerHTML = queues.map(queueCard).join("");
  $("dive-panel").querySelector(".content").innerHTML = queueCard(data.dive_queue);
}

function compareValues(a, b, dir) {
  const left = typeof a === "number" ? a : String(a || "").toLowerCase();
  const right = typeof b === "number" ? b : String(b || "").toLowerCase();
  if (left < right) return dir === "asc" ? -1 : 1;
  if (left > right) return dir === "asc" ? 1 : -1;
  return 0;
}

function selectorForFocus(element) {
  if (!element || element === document.body) return "";
  if (element.id) return `#${CSS.escape(element.id)}`;
  const attrs = ["queuePath", "topicMode", "topicSort", "projectSort", "copy"];
  for (const attr of attrs) {
    const value = element.dataset?.[attr];
    if (value) {
      return `[data-${attr.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`)}="${CSS.escape(value)}"]`;
    }
  }
  return "";
}

function restoreFocus(selector) {
  if (!selector) return;
  const target = document.querySelector(selector);
  if (target && typeof target.focus === "function") {
    target.focus();
  }
}

function topicValue(topic, key) {
  if (key === "queued") return topic.queue ? (topic.queue.unchecked || 0) + (topic.queue.active || 0) + (topic.queue.review || 0) + (topic.queue.failed || 0) : 0;
  if (key === "sources") return topic.sources;
  if (key === "notes") return topic.notes;
  return topic[key] || "";
}

function updateTopicModeButtons() {
  document.querySelectorAll("[data-topic-mode]").forEach((button) => {
    const active = button.dataset.topicMode === state.topicMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function updateSortButtons() {
  document.querySelectorAll("[data-topic-sort]").forEach((button) => {
    const active = button.dataset.topicSort === state.topicSort.key;
    const label = button.dataset.label || button.textContent.trim();
    button.textContent = active ? `${label} ${state.topicSort.dir}` : label;
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.setAttribute("aria-label", active ? `Sort topics by ${label}, currently ${state.topicSort.dir}` : `Sort topics by ${label}`);
  });
  document.querySelectorAll("[data-project-sort]").forEach((button) => {
    const active = button.dataset.projectSort === state.projectSort.key;
    const label = button.dataset.label || button.textContent.trim();
    button.textContent = active ? `${label} ${state.projectSort.dir}` : label;
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.setAttribute("aria-label", active ? `Sort projects by ${label}, currently ${state.projectSort.dir}` : `Sort projects by ${label}`);
  });
}

function topicNameCell(topic) {
  const title = `<span class="hint">${escapeHtml(topic.title)}</span>`;
  if (!topic.queue) {
    return `<strong>${escapeHtml(topic.slug)}</strong>${title}`;
  }
  return `
    <button class="row-button" type="button" data-queue-path="${escapeHtml(topic.queue.path)}">
      <strong>${escapeHtml(topic.slug)}</strong>
      ${title}
    </button>
  `;
}

function renderTopics(data) {
  const filter = state.topicFilter.toLowerCase();
  const rows = data.topics
    .filter((topic) => !filter || topic.slug.toLowerCase().includes(filter) || topic.title.toLowerCase().includes(filter))
    .filter((topic) => {
      if (state.topicMode === "open") return (topic.queue?.unchecked || 0) > 0 || (topic.queue?.active || 0) > 0 || (topic.queue?.review || 0) > 0;
      if (state.topicMode === "failed") return (topic.queue?.failed || 0) > 0;
      return true;
    })
    .sort((a, b) => compareValues(topicValue(a, state.topicSort.key), topicValue(b, state.topicSort.key), state.topicSort.dir));

  $("topics-body").innerHTML = rows
    .map((topic) => {
      const queued = topic.queue ? topic.queue.unchecked : 0;
      const failed = topic.queue ? topic.queue.failed : 0;
      const active = topic.queue ? topic.queue.active : 0;
      const review = topic.queue ? topic.queue.review || 0 : 0;
      return `
        <tr>
          <td>${topicNameCell(topic)}</td>
          <td>${topic.status ? pill(topic.status, topic.status === "active" ? "ok" : "") : '<span class="muted">none</span>'}</td>
          <td>${queued}${active ? ` <span class="count-chip warn">${active} active</span>` : ""}${review ? ` <span class="count-chip warn">${review} review</span>` : ""}${failed ? ` <span class="count-chip bad">${failed} failed</span>` : ""}</td>
          <td>${topic.sources}</td>
          <td>${topic.notes}</td>
          <td>${escapeHtml(topic.modified || "")}</td>
        </tr>
      `;
    })
    .join("") || `<tr><td colspan="6" class="muted">No topics match the current filters.</td></tr>`;
  updateTopicModeButtons();
  updateSortButtons();
}

function projectValue(project, key) {
  const counts = project.todo_counts || {};
  if (key === "open") return counts.unchecked || 0;
  if (key === "active") return counts.active || 0;
  if (key === "review") return counts.review || 0;
  if (key === "blocked") return counts.failed || 0;
  return project[key] || "";
}

function renderProjects(data) {
  const rows = [...data.projects].sort((a, b) => compareValues(projectValue(a, state.projectSort.key), projectValue(b, state.projectSort.key), state.projectSort.dir));
  $("projects-body").innerHTML = rows
    .map((project) => {
      const counts = project.todo_counts || {};
      return `
        <tr>
          <td><strong>${escapeHtml(project.slug)}</strong><span class="hint">${escapeHtml(project.title)}</span></td>
          <td>${project.status ? pill(project.status, project.status === "active" ? "ok" : "") : '<span class="muted">none</span>'}</td>
          <td>${counts.unchecked || 0}</td>
          <td>${counts.active || 0}</td>
          <td>${counts.review || 0}</td>
          <td>${counts.failed || 0}</td>
          <td>${escapeHtml(project.modified || "")}</td>
        </tr>
      `;
    })
    .join("");
  updateSortButtons();
}

function renderLog(data) {
  $("log-panel").querySelector(".content").innerHTML = `
    <div class="list">
      ${data.recent_log.map((entry) => `
        <div class="log-entry">
          <strong>${escapeHtml(entry.title)}</strong>
          <div class="hint">${escapeHtml(entry.summary)}</div>
        </div>
      `).join("")}
    </div>
  `;
}

function statusLabel(status) {
  if (status === "unchecked") return "open";
  if (status === "checked") return "done";
  if (status === "review") return "review";
  return status;
}

function renderDrawer(queue) {
  $("drawer-title").textContent = queue.name;
  $("drawer-subtitle").textContent = queue.path;
  const attention = queueAttentionItems(queue);
  const items = attention.length ? attention : queueItems(queue).slice(0, 12);
  $("drawer-content").innerHTML = `
    <div class="queue-counts">
      <span class="count-chip warn">${queue.unchecked} open</span>
      <span class="count-chip">${queue.checked} done</span>
      <span class="count-chip">${queue.active || 0} active</span>
      <span class="count-chip ${queue.review ? "warn" : ""}">${queue.review || 0} review</span>
      <span class="count-chip ${queue.failed ? "bad" : ""}">${queue.failed} failed</span>
    </div>
    <button class="path-button" type="button" data-copy="${escapeHtml(queue.path)}">Copy source path</button>
    <div class="detail-list">
      ${items.map((item) => `
        <div class="detail-item ${escapeHtml(item.status)}">
          <div class="detail-meta">
            ${pill(statusLabel(item.status), item.status === "failed" ? "blocked" : item.status === "active" || item.status === "review" ? "warn" : "")}
            <span>${escapeHtml(item.section)} | line ${escapeHtml(item.line)}</span>
          </div>
          <div>${escapeHtml(item.text)}</div>
        </div>
      `).join("") || '<div class="muted">No checkbox items found.</div>'}
    </div>
  `;
}

function openDrawer(queue) {
  const drawer = $("detail-drawer");
  state.activeQueuePath = queue.path;
  if (drawer.classList.contains("hidden")) {
    state.previousFocus = document.activeElement;
  }
  document.body.classList.add("drawer-open");
  document.querySelectorAll("body > header, main > :not(#detail-drawer)").forEach((element) => {
    element.setAttribute("inert", "");
  });
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
  $("drawer-title").textContent = queue.name;
  $("drawer-subtitle").textContent = queue.path;
  $("drawer-content").innerHTML = '<div class="muted">Loading queue details...</div>';
  $("drawer-close").focus();
  if (state.data) renderQueues(state.data);
}

function closeDrawer() {
  const drawer = $("detail-drawer");
  drawer.classList.add("hidden");
  drawer.setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
  document.querySelectorAll("body > header, main > :not(#detail-drawer)").forEach((element) => {
    element.removeAttribute("inert");
  });
  if (state.previousFocus && typeof state.previousFocus.focus === "function") {
    state.previousFocus.focus();
  }
  state.previousFocus = null;
}

function trapDrawerFocus(event) {
  const drawer = $("detail-drawer");
  if (event.key !== "Tab" || drawer.classList.contains("hidden")) return;
  const focusable = [...drawer.querySelectorAll(focusableSelector)].filter((element) => element.getClientRects().length);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

async function fetchQueue(path) {
  if (queueCache.has(path)) return queueCache.get(path);
  const response = await fetch(`/api/queue?path=${encodeURIComponent(path)}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const queue = await response.json();
  queueCache.set(path, queue);
  replaceQueue(queue);
  return queue;
}

async function showQueueDetails(path) {
  const summary = findQueue(path) || { name: "Queue", path };
  openDrawer(summary);
  try {
    const queue = await fetchQueue(path);
    renderDrawer(queue);
    if (state.data) renderQueues(state.data);
  } catch (error) {
    $("drawer-content").innerHTML = `<div class="muted">Failed to load queue details: ${escapeHtml(error.message)}</div>`;
  }
}

async function copyValue(value) {
  try {
    await navigator.clipboard.writeText(value);
    showToast("Copied source path");
  } catch {
    window.prompt("Copy this value", value);
    showToast("Copy dialog opened");
  }
}

function showToast(message) {
  const toast = $("toast");
  if (!toast) return;
  window.clearTimeout(state.toastTimer);
  toast.textContent = message;
  toast.classList.remove("hidden");
  state.toastTimer = window.setTimeout(() => {
    toast.classList.add("hidden");
  }, 2200);
}

function renderError(message) {
  $("subhead").textContent = message;
  $("metrics").innerHTML = "";
  $("status-summary").textContent = "Unavailable";
  $("attention-summary").textContent = "Unavailable";
  $("attention-list").innerHTML = `<div class="attention-empty">${escapeHtml(message)}</div>`;
}

function render(data) {
  state.data = data;
  queueCache.clear();
  const generated = new Date(data.generated_at);
  $("subhead").textContent = `${data.kb_root} | refreshed ${generated.toLocaleString()}`;
  renderMetrics(data);
  renderAttention(data);
  renderCheckpoint(data.checkpoint);
  renderKbFeed(data.kb_feed);
  renderQueues(data);
  renderTopics(data);
  renderProjects(data);
  renderLog(data);
}

async function loadStatus(options = {}) {
  const activeFocus = selectorForFocus(document.activeElement);
  $("refresh").disabled = true;
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error ? `${data.error}: ${(data.missing || []).join(", ")}` : `HTTP ${response.status}`);
    }
    render(data);
    restoreFocus(activeFocus);
    if (options.notify) showToast("Status refreshed");
  } catch (error) {
    renderError(`Failed to load status: ${error.message}`);
  } finally {
    $("refresh").disabled = false;
  }
}

$("refresh").addEventListener("click", () => loadStatus({ notify: true }));
$("theme-toggle")?.addEventListener("click", toggleTheme);
$("topic-filter").addEventListener("input", (event) => {
  state.topicFilter = event.target.value;
  if (state.data) renderTopics(state.data);
});
$("drawer-close").addEventListener("click", closeDrawer);

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-queue-path], [data-topic-mode], [data-topic-sort], [data-project-sort], [data-copy]");
  if (!target) return;
  if (target.dataset.queuePath) {
    showQueueDetails(target.dataset.queuePath);
  } else if (target.dataset.topicMode) {
    state.topicMode = target.dataset.topicMode;
    if (state.data) renderTopics(state.data);
  } else if (target.dataset.topicSort) {
    const key = target.dataset.topicSort;
    state.topicSort = { key, dir: state.topicSort.key === key && state.topicSort.dir === "desc" ? "asc" : "desc" };
    if (state.data) renderTopics(state.data);
  } else if (target.dataset.projectSort) {
    const key = target.dataset.projectSort;
    state.projectSort = { key, dir: state.projectSort.key === key && state.projectSort.dir === "desc" ? "asc" : "desc" };
    if (state.data) renderProjects(state.data);
  } else if (target.dataset.copy) {
    copyValue(target.dataset.copy);
  }
});

document.addEventListener("keydown", (event) => {
  trapDrawerFocus(event);
  if (event.key === "Escape" && !$("detail-drawer").classList.contains("hidden")) {
    closeDrawer();
  }
});

applyTheme(preferredTheme());
loadStatus();
setInterval(() => loadStatus(), 30000);
