import assert from "node:assert/strict";
import vm from "node:vm";
import {readFileSync} from "node:fs";

class ClassList {
  constructor() {
    this.values = new Set();
  }

  add(...values) {
    values.forEach((value) => this.values.add(value));
  }

  remove(...values) {
    values.forEach((value) => this.values.delete(value));
  }

  toggle(value, force) {
    if (force === true) this.values.add(value);
    else if (force === false) this.values.delete(value);
    else if (this.values.has(value)) this.values.delete(value);
    else this.values.add(value);
  }
}

class Element {
  constructor() {
    this.classList = new ClassList();
    this.dataset = {};
    this.disabled = false;
    this.innerHTML = "";
    this.textContent = "";
    this.listeners = new Map();
    this.strong = {textContent: ""};
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  querySelector(selector) {
    return selector === "strong" ? this.strong : new Element();
  }
}

const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, new Element());
  return elements.get(id);
};
const pipeline = [new Element(), new Element(), new Element(), new Element()];
const document = {
  addEventListener() {},
  getElementById: element,
  querySelectorAll(selector) {
    if (selector === "#agentPipeline > div") return pipeline;
    return [];
  },
};

let latestStream = null;
class EventSource {
  constructor() {
    latestStream = this;
  }

  close() {}
}

const context = vm.createContext({
  console,
  document,
  EventSource,
  fetch: async () => ({
    ok: true,
    status: 200,
    json: async () => ({mode: "radeon", daily_mode: "radeon"}),
  }),
  setInterval() {},
});
const source = readFileSync(new URL("./demo_stage.js", import.meta.url), "utf8");
vm.runInContext(source, context);
await new Promise((resolve) => setImmediate(resolve));

const badge = element("dailyBackend");
const button = element("dailyButton");
assert.equal(badge.textContent, "AUTO · RADEON ROCm");

button.listeners.get("click")({currentTarget: button});
assert.equal(badge.textContent, "RUNNING · BACKEND UNVERIFIED");
assert.equal(vm.runInContext("dailyBackendPinned", context), true);

await context.updateConnectivity();
assert.equal(badge.textContent, "RUNNING · BACKEND UNVERIFIED");

latestStream.onmessage({
  data: JSON.stringify({
    type: "result",
    report: "# Synthetic report",
    route_metadata: {writer: [{backend: "local_metal"}]},
  }),
});
assert.equal(badge.textContent, "ACTUAL · LOCAL METAL");

latestStream.onmessage({data: JSON.stringify({type: "done"})});
await context.updateConnectivity();
assert.equal(badge.textContent, "ACTUAL · LOCAL METAL");

button.listeners.get("click")({currentTarget: button});
latestStream.onmessage({
  data: JSON.stringify({type: "error", message: "sanitized failure"}),
});
assert.equal(badge.textContent, "RUN FAILED");
latestStream.onmessage({data: JSON.stringify({type: "done"})});
await context.updateConnectivity();
assert.equal(badge.textContent, "RUN FAILED");
