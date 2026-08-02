const scenes = [...document.querySelectorAll(".scene")];
const acts = [...document.querySelectorAll(".act")];
let currentAct = 1;
let timelineLoaded = false;
let connectivityInFlight = false;
let dailyRunActive = false;
let dailyBackendPinned = false;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

function showAct(number) {
  currentAct = Math.max(1, Math.min(6, number));
  acts.forEach((act) => {
    act.classList.toggle("active", Number(act.dataset.act) === currentAct);
  });
  scenes.forEach((scene) => {
    scene.classList.toggle("active", Number(scene.dataset.scene) === currentAct);
  });
  if (currentAct === 2 && !timelineLoaded) loadTimeline();
}

acts.forEach((act) => {
  act.addEventListener("click", () => showAct(Number(act.dataset.act)));
});
document.addEventListener("keydown", (event) => {
  if (event.key === "ArrowRight") showAct(currentAct + 1);
  if (event.key === "ArrowLeft") showAct(currentAct - 1);
});

async function updateConnectivity() {
  if (connectivityInFlight) return;
  connectivityInFlight = true;
  const node = document.getElementById("linkState");
  try {
    const state = await api("/api/connectivity");
    const backend = document.getElementById("dailyBackend");
    node.classList.remove("fallback", "offline");
    if (state.mode === "radeon") {
      node.querySelector("strong").textContent = "RADEON ROCm · ONLINE";
    } else if (state.mode === "local_fallback") {
      node.classList.add("fallback");
      node.querySelector("strong").textContent = "LINK DOWN · LOCAL READY";
    } else {
      node.classList.add("offline");
      node.querySelector("strong").textContent = "NO COMPUTE PATH";
    }
    if (!dailyRunActive && !dailyBackendPinned) {
      if (state.daily_mode === "radeon") {
        backend.textContent = "AUTO · RADEON ROCm";
        backend.classList.remove("fallback");
      } else if (state.daily_mode === "local_fallback") {
        backend.textContent = "AUTO · LOCAL METAL";
        backend.classList.add("fallback");
      } else if (state.daily_mode === "unchecked") {
        backend.textContent = "FAST LINK READY · CHECK BRAIN ON RUN";
        backend.classList.remove("fallback");
      } else {
        backend.textContent = "NO BACKEND";
        backend.classList.add("fallback");
      }
    }
  } catch {
    node.classList.add("offline");
    node.querySelector("strong").textContent = "STATUS UNAVAILABLE";
  } finally {
    connectivityInFlight = false;
  }
}

async function loadTimeline() {
  const container = document.getElementById("timeline");
  try {
    const data = await api("/api/timeline");
    timelineLoaded = true;
    document.getElementById("eventCount").textContent = data.events.length;
    container.innerHTML = data.events.slice(0, 5).map((event, index) => `
      <article class="event" style="animation-delay:${index * 70}ms">
        <time><strong>${escapeHtml(event.time)}</strong><small>${escapeHtml(event.date)}</small></time>
        <div>
          <h3>${escapeHtml(event.activity)}</h3>
          <p>${escapeHtml(event.app)} · ${escapeHtml(event.window_title)}</p>
        </div>
        <div>
          ${(event.topics || []).slice(0, 2).map((topic) => `<span class="tag">${escapeHtml(topic)}</span>`).join("")}
          <div class="event-id">event#${event.id}</div>
        </div>
      </article>
    `).join("");
  } catch (error) {
    container.innerHTML = `<div class="empty-state error-state">${escapeHtml(error.message)}</div>`;
  }
}

document.getElementById("memoryButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const container = document.getElementById("timeline");
  button.disabled = true;
  button.textContent = "REAL PIPELINE RUNNING…";
  container.innerHTML = `<div class="empty-state">sentinel → OCR → novelty → perceive → embed → timeline</div>`;
  try {
    const data = await api("/api/memory-growth", {method: "POST"});
    if (!data.pass) throw new Error("memory pipeline did not pass");
    timelineLoaded = false;
    await loadTimeline();
    button.textContent = `${data.created} REAL EVENTS CREATED`;
  } catch (error) {
    container.innerHTML = `<div class="empty-state error-state">${escapeHtml(error.message)}</div>`;
    button.textContent = "INGEST FAILED";
  } finally {
    button.disabled = false;
  }
});

document.getElementById("sentinelButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "CLASSIFYING REAL FIXTURE…";
  document.getElementById("sentinelDecision").textContent = "RUNNING";
  try {
    const data = await api("/api/sentinel", {method: "POST"});
    const audit = data.audit;
    const ackSentinel = data.ack.sentinel || {};
    document.getElementById("sentinelDecision").textContent = audit.decision.toUpperCase();
    document.getElementById("sentinelCategory").textContent = audit.category;
    document.getElementById("sentinelConfidence").textContent = `${(audit.confidence * 100).toFixed(1)}%`;
    document.getElementById("classifyProof").textContent =
      ackSentinel.decision === "block" ? "BLOCKED" : "FAILED";
    document.getElementById("pixelsProof").textContent =
      data.new_screenshot_files.length === 0 ? "0 FILES" : `${data.new_screenshot_files.length} FILES`;
    document.getElementById("timelineProof").textContent = `${data.timeline_rows} ROWS`;
    document.getElementById("auditProof").textContent = audit.id ? `AUDIT #${audit.id}` : "MISSING";
    document.querySelectorAll(".privacy-proof i").forEach((node) => {
      node.className = data.pass ? "stop" : "error-state";
    });
    document.getElementById("classifyProof").className = data.pass ? "pass" : "error-state";
    document.getElementById("auditProof").className = data.pass ? "pass" : "error-state";
    document.getElementById("sentinelAudit").textContent =
      `${audit.ts} · ${data.device_id} · ${audit.category} · ${audit.decision} · AUDIT_ROWS=${data.audit_rows} · PASS=${data.pass}`;
  } catch (error) {
    document.getElementById("sentinelDecision").textContent = "ERROR";
    document.getElementById("sentinelAudit").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "RUN REAL SENTINEL";
  }
});

document.getElementById("recallButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const result = document.getElementById("recallResult");
  button.disabled = true;
  button.textContent = "AGENT SEARCHING…";
  try {
    const data = await api("/api/recall", {method: "POST"});
    const bbox = (data.highlights || [])[0]?.bbox || [];
    let highlight = "";
    if (bbox.length === 4) {
      const [x1, y1, x2, y2] = bbox;
      highlight = `<span class="bbox" style="
        left:${(100 * x1 / data.image_width).toFixed(3)}%;
        top:${(100 * y1 / data.image_height).toFixed(3)}%;
        width:${(100 * (x2 - x1) / data.image_width).toFixed(3)}%;
        height:${(100 * (y2 - y1) / data.image_height).toFixed(3)}%;
      "></span>`;
    }
    result.innerHTML = `
      <figure class="evidence-frame">
        <img src="${escapeHtml(data.image_url)}" alt="Synthetic PR evidence" />${highlight}
      </figure>
      <article class="recall-answer">
        <small>AGENTD · ALL-CITATION GATE → RESOLVED EVIDENCE</small>
        <h3>${escapeHtml(data.answer)}</h3>
        <span class="citation">${escapeHtml(data.citation)}</span>
        <div class="evidence-meta">
          ${escapeHtml(data.date)} · ${escapeHtml(data.time)}<br />
          ${escapeHtml(data.window_title)}<br />${escapeHtml(data.url)}
        </div>
      </article>`;
  } catch (error) {
    result.innerHTML = `<div class="empty-state error-state">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = "ASK DEJAVIEW";
  }
});

document.getElementById("preferenceButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const result = document.getElementById("preferenceResult");
  button.disabled = true;
  button.textContent = "DIALECTIC RUNNING…";
  result.innerHTML = `<div class="empty-state">Honcho is reasoning over the isolated synthetic profile…</div>`;
  try {
    const data = await api("/api/preference", {method: "POST"});
    result.innerHTML = `
      <small>HONCHO DIALECTIC · ${data.derived_conclusions} DERIVED SYNTHETIC CONCLUSIONS</small>
      <p>${escapeHtml(data.answer || "No answer returned.")}</p>`;
  } catch (error) {
    result.innerHTML = `<div class="empty-state error-state">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = "ASK HONCHO USER MODEL";
  }
});

document.getElementById("disconnectButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "DISCONNECTING VERIFIED TUNNEL…";
  try {
    await api("/api/connectivity/disconnect", {method: "POST"});
    button.textContent = "RADEON LINK DISCONNECTED";
    await updateConnectivity();
  } catch (error) {
    button.textContent = `DISCONNECT FAILED · ${error.message}`;
    button.disabled = false;
  }
});

document.getElementById("dailyButton").addEventListener("click", (event) => {
  const button = event.currentTarget;
  const trace = document.getElementById("dailyTrace");
  const report = document.getElementById("dailyReport");
  const backend = document.getElementById("dailyBackend");
  const pipeline = [...document.querySelectorAll("#agentPipeline > div")];
  button.disabled = true;
  dailyRunActive = true;
  dailyBackendPinned = true;
  button.textContent = "AGENTS WORKING…";
  backend.textContent = "RUNNING · BACKEND UNVERIFIED";
  backend.classList.remove("fallback");
  trace.textContent = "Routing to the best available inference path…";
  report.textContent = "Grounding report against synthetic demo events…";
  pipeline.forEach((node) => node.classList.remove("active"));

  const stream = new EventSource("/api/daily/stream");
  let traceLines = [];
  stream.onmessage = (message) => {
    const data = JSON.parse(message.data);
    if (data.type === "trace") {
      traceLines.push(data.line);
      trace.innerHTML = traceLines.map((line) => {
        return escapeHtml(line).replace(/^(\[[^\]]+\])/, "<b>$1</b>");
      }).join("<br />");
      const stageName = (data.line.match(/^\[([^\]]+)\]/) || [])[1];
      const stageIndex = {Planner: 0, Retriever: 1, Writer: 2, Reviewer: 3}[stageName];
      if (stageIndex !== undefined) pipeline[stageIndex].classList.add("active");
    } else if (data.type === "result") {
      report.textContent = data.report;
      pipeline.forEach((node) => node.classList.add("active"));
      const actualRoutes = data.route_metadata || {};
      const writerRoutes = actualRoutes.writer || [];
      const finalWriterRoute = writerRoutes[writerRoutes.length - 1] ||
        (actualRoutes.reviewer || [])[0] || null;
      if (finalWriterRoute) {
        const badge = document.getElementById("dailyBackend");
        const local = finalWriterRoute.backend === "local_metal";
        badge.textContent = local ? "ACTUAL · LOCAL METAL" : "ACTUAL · RADEON ROCm";
        badge.classList.toggle("fallback", local);
      }
      dailyBackendPinned = true;
    } else if (data.type === "error") {
      trace.innerHTML += `<br /><span class="error-state">${escapeHtml(data.message)}</span>`;
      report.textContent = "Run did not produce a report.";
      const badge = document.getElementById("dailyBackend");
      badge.textContent = "RUN FAILED";
      badge.classList.add("fallback");
      dailyBackendPinned = true;
    } else if (data.type === "done") {
      stream.close();
      dailyRunActive = false;
      button.disabled = false;
      button.textContent = "RUN AGENTIC DAILY";
    }
  };
  stream.onerror = () => {
    stream.close();
    if (button.disabled) {
      dailyRunActive = false;
      dailyBackendPinned = true;
      trace.innerHTML += `<br /><span class="error-state">Inference stream closed before completion.</span>`;
      const badge = document.getElementById("dailyBackend");
      badge.textContent = "STREAM FAILED";
      badge.classList.add("fallback");
      button.disabled = false;
      button.textContent = "RUN AGENTIC DAILY";
    }
  };
});

updateConnectivity();
setInterval(updateConnectivity, 5000);
