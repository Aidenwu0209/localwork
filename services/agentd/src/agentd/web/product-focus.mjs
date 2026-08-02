export function keepDialogFocus(event, { drawer, activeElement, onEscape }) {
  if (drawer.hidden) return false;
  if (event.key === "Escape") {
    event.preventDefault();
    onEscape();
    return true;
  }
  if (event.key !== "Tab") return false;

  const focusable = [...drawer.querySelectorAll(
    "button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
  )];
  if (!focusable.length) return false;

  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const enteredAtBoundary = activeElement === drawer || !drawer.contains(activeElement);
  if (enteredAtBoundary) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
    return true;
  }
  if (event.shiftKey && activeElement === first) {
    event.preventDefault();
    last.focus();
    return true;
  }
  if (!event.shiftKey && activeElement === last) {
    event.preventDefault();
    first.focus();
    return true;
  }
  return false;
}

export function setDialogBackgroundInert(regions, inert) {
  for (const region of regions) {
    if (!region) continue;
    region.inert = inert;
    if (inert) {
      region.setAttribute("aria-hidden", "true");
    } else {
      region.removeAttribute("aria-hidden");
    }
  }
}

export function setProfileControls(controls, { available, enabled, paused } = {}) {
  controls.ask.disabled = !available;
  controls.pause.disabled = !available || !enabled || Boolean(paused);
  controls.question.disabled = !available;
  controls.resume.disabled = !available || !paused;
}

export function evidenceImageAlt({ eventId, app, captured }) {
  const safeEvent = Number.isInteger(eventId) && eventId > 0 ? eventId : "unknown";
  const safeApp = typeof app === "string" && app.trim() ? app.trim() : "unknown application";
  const safeCaptured = typeof captured === "string" && captured.trim()
    ? captured.trim()
    : "an unknown time";
  return `Screen evidence for event ${safeEvent} in ${safeApp}, captured ${safeCaptured}.`;
}

export function shouldAnnounceStatus(previous, next) {
  return previous !== next;
}
