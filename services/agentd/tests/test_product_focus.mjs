import assert from "node:assert/strict";
import test from "node:test";

import {
  evidenceImageAlt,
  keepDialogFocus,
  profileStateOperational,
  setDialogBackgroundInert,
  setProfileControls,
  shouldAnnounceStatus,
} from "../src/agentd/web/product-focus.mjs";

function dialogFixture(active = "drawer", shiftKey = false, key = "Tab") {
  const focused = [];
  const first = { name: "close", focus: () => focused.push("close") };
  const last = { name: "last", focus: () => focused.push("last") };
  const drawer = {
    hidden: false,
    querySelectorAll: () => [first, last],
    contains: (node) => node === first || node === last,
  };
  const activeElement = { drawer, first, last, outside: { name: "resume-profile" } }[active];
  const event = {
    key,
    shiftKey,
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
  return { activeElement, drawer, event, first, focused, last };
}

test("first Shift+Tab from the dialog container stays inside", () => {
  const fixture = dialogFixture("drawer", true);

  keepDialogFocus(fixture.event, {
    drawer: fixture.drawer,
    activeElement: fixture.activeElement,
    onEscape: () => {},
  });

  assert.equal(fixture.event.prevented, true);
  assert.deepEqual(fixture.focused, ["last"]);
});

test("Tab from the last control loops to Close", () => {
  const fixture = dialogFixture("last");

  keepDialogFocus(fixture.event, {
    drawer: fixture.drawer,
    activeElement: fixture.activeElement,
    onEscape: () => {},
  });

  assert.equal(fixture.event.prevented, true);
  assert.deepEqual(fixture.focused, ["close"]);
});

test("Escape invokes close and restores the evidence trigger", () => {
  const fixture = dialogFixture("first", false, "Escape");
  const trigger = { focus: () => fixture.focused.push("trigger") };

  keepDialogFocus(fixture.event, {
    drawer: fixture.drawer,
    activeElement: fixture.activeElement,
    onEscape: () => trigger.focus(),
  });

  assert.equal(fixture.event.prevented, true);
  assert.deepEqual(fixture.focused, ["trigger"]);
});

function fakeBackground() {
  const attributes = new Set();
  return {
    attributes,
    inert: false,
    setAttribute: (name) => attributes.add(name),
    removeAttribute: (name) => attributes.delete(name),
  };
}

test("dialog background becomes inert and aria-hidden, then fully restores", () => {
  const regions = [fakeBackground(), fakeBackground(), fakeBackground()];

  setDialogBackgroundInert(regions, true);
  for (const region of regions) {
    assert.equal(region.inert, true);
    assert.equal(region.attributes.has("aria-hidden"), true);
  }

  setDialogBackgroundInert(regions, false);
  for (const region of regions) {
    assert.equal(region.inert, false);
    assert.equal(region.attributes.has("aria-hidden"), false);
  }
});

test("unavailable profile disables every profile interaction", () => {
  const controls = { ask: {}, pause: {}, question: {}, resume: {} };

  setProfileControls(controls, { available: false });

  assert.equal(controls.ask.disabled, true);
  assert.equal(controls.pause.disabled, true);
  assert.equal(controls.question.disabled, true);
  assert.equal(controls.resume.disabled, true);
});

test("only verified or intentionally paused profile states are operational", () => {
  assert.equal(profileStateOperational("ready"), true);
  assert.equal(profileStateOperational("stale"), true);
  for (const state of ["degraded", "offline", "unknown", "invalid"]) {
    assert.equal(profileStateOperational(state), false, state);
  }
});

test("available profile exposes only controls valid for its current state", () => {
  const controls = { ask: {}, pause: {}, question: {}, resume: {} };

  setProfileControls(controls, { available: true, enabled: true, paused: false });
  assert.equal(controls.ask.disabled, false);
  assert.equal(controls.pause.disabled, false);
  assert.equal(controls.question.disabled, false);
  assert.equal(controls.resume.disabled, true);

  setProfileControls(controls, { available: true, enabled: true, paused: true });
  assert.equal(controls.ask.disabled, false);
  assert.equal(controls.pause.disabled, true);
  assert.equal(controls.resume.disabled, false);
});

test("evidence image alt identifies event, app, and captured time", () => {
  assert.equal(
    evidenceImageAlt({ eventId: 315, app: "VS Code", captured: "Aug 3, 09:18" }),
    "Screen evidence for event 315 in VS Code, captured Aug 3, 09:18.",
  );
});

test("identical status state and text are a no-op for live regions", () => {
  assert.equal(shouldAnnounceStatus("Capture ready", "Capture ready"), false);
  assert.equal(shouldAnnounceStatus("Capture unknown", "Capture ready"), true);
});
