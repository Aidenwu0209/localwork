import {
  evidenceImageAlt,
  keepDialogFocus,
  profileStateOperational,
  setDialogBackgroundInert,
  setProfileControls,
  shouldAnnounceStatus,
} from "/product-focus.mjs";

const state = {
  csrf: null,
  cursor: null,
  filters: {},
  evidenceTrigger: null,
  highlights: [],
};

const byId = (id) => document.getElementById(id);
const dialogBackground = [
  document.querySelector(".topbar"),
  document.querySelector(".view-nav"),
  byId("main"),
];
const profileControls = {
  ask: byId("ask-profile"),
  pause: byId("pause-profile"),
  question: byId("profile-question"),
  resume: byId("resume-profile"),
};
const humanErrors = {
  answer_unavailable: "An answer is not available right now. Your timeline remains unchanged.",
  compute_unavailable: "The answer engine is offline. Try again after its status recovers.",
  evidence_not_found: "This evidence link is unavailable or has expired. Refresh the timeline and try again.",
  evidence_unavailable: "Evidence cannot be loaded right now.",
  evidence_image_unavailable: "The source image is unavailable. Metadata may still be shown.",
  invalid_profile_query: "Enter a short profile question and try again.",
  invalid_question: "Enter a short memory question and try again.",
  invalid_timeline_query: "Check the timeline filters and try again.",
  privacy_summary_unavailable: "The privacy ledger is unavailable right now.",
  profile_unavailable: "The profile service is unavailable right now.",
  timeline_unavailable: "Timeline data is unavailable right now.",
};

function showError(element, message) {
  element.textContent = message;
  element.hidden = false;
}

function clearError(element) {
  element.textContent = "";
  element.hidden = true;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) {
    let code = "request_failed";
    try {
      const body = await response.json();
      code = body?.detail?.code || code;
    } catch (_error) {
      // Keep a stable local error instead of surfacing upstream content.
    }
    const error = new Error(humanErrors[code] || "This local service is unavailable right now.");
    error.code = code;
    throw error;
  }
  return response.json();
}

async function securePost(path, body) {
  if (!state.csrf) await loadSession();
  return api(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-DejaView-CSRF": state.csrf,
    },
    body: JSON.stringify(body),
  });
}

async function loadSession() {
  const body = await api("/api/session");
  state.csrf = body.csrf_token;
}

function stateLabel(raw) {
  const value = ["ready", "degraded", "stale", "offline", "unknown"].includes(raw) ? raw : "unknown";
  const icon = { ready: "✓", degraded: "!", stale: "!", offline: "×", unknown: "?" }[value];
  return { value, icon, text: value.charAt(0).toUpperCase() + value.slice(1) };
}

function setStatusChip(element, label, rawState) {
  const status = stateLabel(rawState);
  const announcement = `${label} ${status.text.toLowerCase()}`;
  if (!shouldAnnounceStatus(element.dataset.announcement, announcement)) return;
  element.dataset.announcement = announcement;
  element.dataset.state = status.value;
  element.replaceChildren();
  const icon = document.createElement("i");
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = status.icon;
  element.append(icon, document.createTextNode(` ${announcement}`));
}

function setDefinitionList(element, rows) {
  element.replaceChildren();
  for (const [term, value] of rows) {
    const row = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    row.append(dt, dd);
    element.append(row);
  }
}

async function loadStatus() {
  const error = byId("status-error");
  clearError(error);
  try {
    const body = await api("/api/status");
    setStatusChip(byId("overall-status"), "System", body.overall);
    setStatusChip(byId("capture-status"), "Capture", body.capture?.state);
    setStatusChip(byId("compute-status"), "Compute", body.compute?.state);
    const checked = body.last_checked_at ? new Date(body.last_checked_at) : null;
    byId("last-checked").textContent = checked && !Number.isNaN(checked.valueOf())
      ? `Checked ${checked.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
      : "Check time unknown";
    const heartbeat = body.capture?.age_seconds == null
      ? stateLabel(body.capture?.state).text
      : `${Math.round(body.capture.age_seconds)}s ago · ${stateLabel(body.capture.state).text}`;
    setDefinitionList(byId("status-detail"), [
      ["Database", stateLabel(body.components?.database?.state).text],
      ["Memory service", stateLabel(body.components?.memoryd?.state).text],
      ["Capture heartbeat", heartbeat],
      ["Answer engine", stateLabel(body.compute?.state).text],
    ]);
  } catch (failure) {
    setStatusChip(byId("overall-status"), "System", "offline");
    setStatusChip(byId("capture-status"), "Capture", "unknown");
    setStatusChip(byId("compute-status"), "Compute", "unknown");
    byId("last-checked").textContent = "Check failed";
    showError(error, failure.message);
  }
}

function collectFilters() {
  const data = new FormData(byId("filter-form"));
  const filters = {};
  for (const [key, value] of data.entries()) {
    const cleaned = String(value).trim();
    if (cleaned) filters[key] = cleaned;
  }
  return filters;
}

function formatWhen(raw) {
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.valueOf())) return "Time unknown";
  return parsed.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function renderTimelineItem(item) {
  const li = document.createElement("li");
  li.className = "timeline-item";
  const header = document.createElement("header");
  const app = document.createElement("span");
  const when = document.createElement("time");
  app.textContent = typeof item.app === "string" && item.app ? item.app : "Unknown application";
  when.textContent = formatWhen(item.ts);
  if (typeof item.ts === "string") when.dateTime = item.ts;
  header.append(app, when);
  const title = document.createElement("h2");
  title.textContent = typeof item.activity === "string" && item.activity ? item.activity : "Captured activity";
  const topics = document.createElement("p");
  topics.textContent = Array.isArray(item.topics) && item.topics.length ? item.topics.join(" · ") : "No topic labels";
  li.append(header, title, topics);
  if (item.evidence?.available && typeof item.evidence.url === "string") {
    const button = document.createElement("button");
    button.className = "evidence-button";
    button.type = "button";
    button.textContent = "Inspect evidence";
    button.addEventListener("click", () => openEvidence(item.evidence.url, button));
    li.append(button);
  }
  return li;
}

async function loadTimeline({ append = false } = {}) {
  const list = byId("timeline-list");
  const message = byId("timeline-message");
  const error = byId("timeline-error");
  const more = byId("load-more");
  clearError(error);
  message.hidden = false;
  message.textContent = append ? "Loading earlier activity…" : "Loading recent activity…";
  more.disabled = true;
  const params = new URLSearchParams({ ...state.filters, limit: "20" });
  if (append && state.cursor) params.set("cursor", state.cursor);
  try {
    const body = await api(`/api/timeline?${params}`);
    if (!append) list.replaceChildren();
    const items = Array.isArray(body.items) ? body.items : [];
    for (const item of items) list.append(renderTimelineItem(item));
    state.cursor = typeof body.next_cursor === "string" ? body.next_cursor : null;
    more.hidden = !state.cursor;
    more.disabled = false;
    message.textContent = list.children.length
      ? `${list.children.length} ${list.children.length === 1 ? "memory" : "memories"} shown.`
      : "No captured activity matches these filters.";
  } catch (failure) {
    message.textContent = append ? "Earlier activity was not loaded." : "Timeline unavailable.";
    showError(error, failure.message);
    more.hidden = true;
  }
}

function renderAnswer(body) {
  const card = byId("answer-card");
  card.replaceChildren();
  const title = document.createElement("h3");
  title.textContent = body.evidence_insufficient ? "Evidence is insufficient" : "Answer";
  const answer = document.createElement("p");
  answer.textContent = typeof body.answer === "string" ? body.answer : "No verified answer was returned.";
  card.append(title, answer);
  const citations = Array.isArray(body.citations) ? body.citations : [];
  if (citations.length) {
    const group = document.createElement("div");
    group.className = "citation-list";
    group.setAttribute("aria-label", "Answer evidence");
    for (const citation of citations) {
      if (typeof citation.evidence_url !== "string") continue;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = typeof citation.label === "string" ? citation.label : `Event ${citation.event_id}`;
      button.addEventListener("click", () => openEvidence(citation.evidence_url, button));
      group.append(button);
    }
    card.append(group);
  }
}

async function askMemory(event) {
  event.preventDefault();
  const question = byId("ask-question").value.trim();
  if (!question) return;
  const error = byId("ask-error");
  const card = byId("answer-card");
  const submit = event.currentTarget.querySelector("button[type='submit']");
  clearError(error);
  submit.disabled = true;
  card.setAttribute("aria-busy", "true");
  card.textContent = "Checking your verified memories…";
  try {
    renderAnswer(await securePost("/api/ask", { question }));
    await loadStatus();
  } catch (failure) {
    card.textContent = "No answer was added.";
    showError(error, failure.message);
  } finally {
    submit.disabled = false;
    card.setAttribute("aria-busy", "false");
  }
}

async function loadPrivacy() {
  const error = byId("privacy-error");
  clearError(error);
  try {
    const body = await api("/api/privacy/summary");
    const total = Number.isFinite(body.total) ? body.total : 0;
    const blocked = Number.isFinite(body.blocked) ? body.blocked : 0;
    const allowed = Number.isFinite(body.allowed) ? body.allowed : 0;
    byId("blocked-count").textContent = String(blocked);
    byId("privacy-summary").textContent = `${total} decisions recorded locally: ${allowed} allowed and ${blocked} blocked.`;
  } catch (failure) {
    byId("blocked-count").textContent = "—";
    byId("privacy-summary").textContent = "Privacy decisions could not be verified.";
    showError(error, failure.message);
  }
}

async function loadProfileStatus() {
  const error = byId("profile-error");
  clearError(error);
  try {
    const body = await api("/api/profile/status");
    const raw = ["ready", "degraded", "stale", "offline", "unknown"].includes(body.state)
      ? body.state
      : "unknown";
    const label = raw === "ready" ? "Active" : raw === "stale" ? "Paused" : raw === "offline" ? "Disabled" : raw === "degraded" ? "Needs attention" : "Unverified";
    setStatusChip(byId("profile-status"), label, raw);
    setProfileControls(profileControls, {
      available: profileStateOperational(raw),
      enabled: body.enabled,
      paused: body.paused,
    });
  } catch (failure) {
    setStatusChip(byId("profile-status"), "Profile", "unknown");
    setProfileControls(profileControls, { available: false });
    showError(error, failure.message);
  }
}

async function askProfile(event) {
  event.preventDefault();
  const question = byId("profile-question").value.trim();
  if (!question) return;
  const error = byId("profile-error");
  const answer = byId("profile-answer");
  const submit = event.currentTarget.querySelector("button[type='submit']");
  clearError(error);
  submit.disabled = true;
  answer.textContent = "Checking the local projection…";
  try {
    const body = await securePost("/api/profile/query", { question });
    answer.textContent = typeof body.answer === "string" ? body.answer : "No projection was returned.";
  } catch (failure) {
    answer.textContent = "";
    showError(error, failure.message);
  } finally {
    submit.disabled = false;
  }
}

async function controlProfile(action) {
  const verb = action === "pause" ? "pause" : "resume";
  if (!window.confirm(`Confirm you want to ${verb} local profile projection?`)) return;
  const error = byId("profile-error");
  clearError(error);
  try {
    await securePost(`/api/profile/${action}`, { confirm: true });
    await loadProfileStatus();
  } catch (failure) {
    showError(error, failure.message);
  }
}

function metadataRow(term, value) {
  const row = document.createElement("div");
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = term;
  dd.textContent = value;
  row.append(dt, dd);
  return row;
}

function drawHighlights() {
  const layer = byId("highlight-layer");
  const image = byId("evidence-image");
  layer.replaceChildren();
  if (!image.naturalWidth || !image.naturalHeight) return;
  for (const item of state.highlights) {
    const bbox = item?.bbox;
    if (!Array.isArray(bbox) || bbox.length !== 4 || !bbox.every(Number.isFinite)) continue;
    const [left, top, right, bottom] = bbox;
    if (right <= left || bottom <= top) continue;
    const box = document.createElement("span");
    box.className = "highlight-box";
    box.style.left = `${Math.max(0, left / image.naturalWidth * 100)}%`;
    box.style.top = `${Math.max(0, top / image.naturalHeight * 100)}%`;
    box.style.width = `${Math.min(100, (right - left) / image.naturalWidth * 100)}%`;
    box.style.height = `${Math.min(100, (bottom - top) / image.naturalHeight * 100)}%`;
    layer.append(box);
  }
}

async function openEvidence(url, trigger) {
  state.evidenceTrigger = trigger;
  const drawer = byId("evidence-drawer");
  const backdrop = byId("evidence-backdrop");
  const message = byId("evidence-message");
  const error = byId("evidence-error");
  const figure = byId("evidence-figure");
  clearError(error);
  message.hidden = false;
  message.textContent = "Loading evidence…";
  figure.hidden = true;
  byId("evidence-meta").replaceChildren();
  state.highlights = [];
  drawer.hidden = false;
  backdrop.hidden = false;
  setDialogBackgroundInert(dialogBackground, true);
  document.body.classList.add("drawer-open");
  byId("close-evidence").focus();
  try {
    const body = await api(url);
    const meta = byId("evidence-meta");
    meta.append(
      metadataRow("Application", typeof body.app === "string" ? body.app : "Unknown"),
      metadataRow("Captured", formatWhen(body.ts)),
      metadataRow("Activity", typeof body.activity === "string" ? body.activity : "Not described"),
      metadataRow("Topics", Array.isArray(body.topics) && body.topics.length ? body.topics.join(", ") : "None"),
      metadataRow("Event", String(body.event_id ?? "Unknown")),
    );
    state.highlights = Array.isArray(body.highlights) ? body.highlights : [];
    if (body.image?.available && typeof body.image.url === "string") {
      const image = byId("evidence-image");
      image.alt = evidenceImageAlt({
        eventId: body.event_id,
        app: body.app,
        captured: formatWhen(body.ts),
      });
      image.onload = drawHighlights;
      image.onerror = () => showError(error, humanErrors.evidence_image_unavailable);
      image.src = body.image.url;
      byId("evidence-caption").textContent = `${state.highlights.length} verified text ${state.highlights.length === 1 ? "region" : "regions"} highlighted.`;
      figure.hidden = false;
    }
    message.textContent = body.image?.available ? "Evidence loaded from this device." : "Metadata is available; the source image is not.";
  } catch (failure) {
    message.textContent = "Evidence was not loaded.";
    showError(error, failure.message);
  }
}

function closeEvidence() {
  byId("evidence-drawer").hidden = true;
  byId("evidence-backdrop").hidden = true;
  setDialogBackgroundInert(dialogBackground, false);
  document.body.classList.remove("drawer-open");
  byId("evidence-image").removeAttribute("src");
  if (state.evidenceTrigger) state.evidenceTrigger.focus();
  state.evidenceTrigger = null;
}

function trapDrawerFocus(event) {
  const drawer = byId("evidence-drawer");
  keepDialogFocus(event, {
    drawer,
    activeElement: document.activeElement,
    onEscape: closeEvidence,
  });
}

byId("filter-form").addEventListener("submit", (event) => {
  event.preventDefault();
  state.filters = collectFilters();
  state.cursor = null;
  loadTimeline();
});
byId("refresh-timeline").addEventListener("click", () => { state.cursor = null; loadTimeline(); });
byId("load-more").addEventListener("click", () => loadTimeline({ append: true }));
byId("ask-form").addEventListener("submit", askMemory);
byId("profile-form").addEventListener("submit", askProfile);
byId("pause-profile").addEventListener("click", () => controlProfile("pause"));
byId("resume-profile").addEventListener("click", () => controlProfile("resume"));
byId("refresh-status").addEventListener("click", loadStatus);
byId("close-evidence").addEventListener("click", closeEvidence);
byId("evidence-backdrop").addEventListener("click", closeEvidence);
document.addEventListener("keydown", trapDrawerFocus);
window.addEventListener("resize", drawHighlights);

Promise.allSettled([
  loadSession(),
  loadStatus(),
  loadTimeline(),
  loadPrivacy(),
  loadProfileStatus(),
]);
window.setInterval(loadStatus, 15000);
