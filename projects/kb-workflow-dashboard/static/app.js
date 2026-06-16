const state = {
  data: null,
  topicFilter: "",
};

const $ = (id) => document.getElementById(id);

function sum(items, getter) {
  return items.reduce((total, item) => total + getter(item), 0);
}

function pill(text, kind = "") {
  return `<span class="status-pill ${kind}">${escapeHtml(text || "unknown")}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function metric(label, value) {
  return `<div class="metric"><span class="value">${escapeHtml(value)}</span><span class="label">${escapeHtml(label)}</span></div>`;
}

function queueCard(queue) {
  const sections = queue.sections
    .filter((section) => section.unchecked || section.failed || section.active)
    .slice(0, 4)
    .map((section) => `<div class="hint">${escapeHtml(section.heading)}: ${section.unchecked} open, ${section.failed} failed</div>`)
    .join("");
  return `
    <div class="queue-card">
      <h3>${escapeHtml(queue.name)}</h3>
      <div class="hint">${escapeHtml(queue.path)}</div>
      <div class="queue-counts">
        <span class="count-chip warn">${queue.unchecked} open</span>
        <span class="count-chip">${queue.checked} done</span>
        <span class="count-chip ${queue.failed ? "bad" : ""}">${queue.failed} failed</span>
      </div>
      ${sections ? `<div class="list" style="margin-top:10px">${sections}</div>` : ""}
    </div>
  `;
}

function renderMetrics(data) {
  const topicOpen = sum(data.topics, (topic) => topic.queue ? topic.queue.unchecked : 0);
  const topicFailed = sum(data.topics, (topic) => topic.queue ? topic.queue.failed : 0);
  const projectOpen = sum(data.projects, (project) => project.todo_counts?.unchecked || 0);
  const globalOpen = sum(data.global_queues, (queue) => queue.unchecked);
  $("metrics").innerHTML = [
    metric("Topics", data.topics.length),
    metric("Projects", data.projects.length),
    metric("Topic Queue Open", topicOpen),
    metric("Global Queue Open", globalOpen),
    metric("Topic Queue Failed", topicFailed),
    metric("Project Todos", projectOpen),
  ].join("");
}

function renderCheckpoint(checkpoint) {
  const kind = checkpoint.is_active ? "active" : checkpoint.status === "complete" ? "ok" : "warn";
  $("checkpoint-panel").querySelector(".content").innerHTML = `
    <div class="list">
      <div>${pill(checkpoint.status, kind)}</div>
      <div><strong>${escapeHtml(checkpoint.operation)}</strong></div>
      <div class="hint">${escapeHtml(checkpoint.path)}${checkpoint.last_updated ? ` · ${escapeHtml(checkpoint.last_updated)}` : ""}</div>
      <div class="queue-counts">
        <span class="count-chip">${checkpoint.counts.unchecked} pending</span>
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
      <div class="hint">${escapeHtml(feed.path)}</div>
      <div><strong>Outbox</strong><div class="queue-counts">${outbox || '<span class="muted">none</span>'}</div></div>
      <div><strong>Capture</strong><div class="queue-counts">${capture || '<span class="muted">none</span>'}</div></div>
      <div class="hint">Last activity: ${escapeHtml(meta.last_activity_at || "unknown")}</div>
      <div class="hint">Last KB commit: ${escapeHtml(meta.last_kb_commit_at || "unknown")}</div>
    </div>
  `;
}

function renderQueues(data) {
  const queues = [...data.global_queues, data.dive_queue];
  $("global-queues").innerHTML = queues.map(queueCard).join("");
  $("dive-panel").querySelector(".content").innerHTML = queueCard(data.dive_queue);
}

function renderTopics(data) {
  const filter = state.topicFilter.toLowerCase();
  $("topics-body").innerHTML = data.topics
    .filter((topic) => !filter || topic.slug.toLowerCase().includes(filter) || topic.title.toLowerCase().includes(filter))
    .map((topic) => {
      const queued = topic.queue ? topic.queue.unchecked : 0;
      const failed = topic.queue ? topic.queue.failed : 0;
      return `
        <tr>
          <td><strong>${escapeHtml(topic.slug)}</strong></td>
          <td>${topic.status ? pill(topic.status, topic.status === "active" ? "ok" : "") : '<span class="muted">none</span>'}</td>
          <td>${queued}${failed ? ` <span class="count-chip bad">${failed} failed</span>` : ""}</td>
          <td>${topic.sources}</td>
          <td>${topic.notes}</td>
          <td>${escapeHtml(topic.modified || "")}</td>
        </tr>
      `;
    })
    .join("");
}

function renderProjects(data) {
  $("projects-body").innerHTML = data.projects
    .map((project) => {
      const counts = project.todo_counts || {};
      return `
        <tr>
          <td><strong>${escapeHtml(project.slug)}</strong></td>
          <td>${project.status ? pill(project.status, project.status === "active" ? "ok" : "") : '<span class="muted">none</span>'}</td>
          <td>${counts.unchecked || 0}</td>
          <td>${counts.active || 0}</td>
          <td>${counts.failed || 0}</td>
          <td>${escapeHtml(project.modified || "")}</td>
        </tr>
      `;
    })
    .join("");
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

function render(data) {
  state.data = data;
  const generated = new Date(data.generated_at);
  $("subhead").textContent = `${data.kb_root} · refreshed ${generated.toLocaleString()}`;
  renderMetrics(data);
  renderCheckpoint(data.checkpoint);
  renderKbFeed(data.kb_feed);
  renderQueues(data);
  renderTopics(data);
  renderProjects(data);
  renderLog(data);
}

async function loadStatus() {
  $("refresh").disabled = true;
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    $("subhead").textContent = `Failed to load status: ${error.message}`;
  } finally {
    $("refresh").disabled = false;
  }
}

$("refresh").addEventListener("click", loadStatus);
$("topic-filter").addEventListener("input", (event) => {
  state.topicFilter = event.target.value;
  if (state.data) renderTopics(state.data);
});

loadStatus();
setInterval(loadStatus, 30000);

