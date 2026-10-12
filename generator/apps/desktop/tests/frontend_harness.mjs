// Headless host for the Disknot views. Pytest starts this with node.
// One scenario per process so module state stays fresh.

import { register } from "node:module";

await register(new URL("./frontend_loader.mjs", import.meta.url), {
  parentURL: import.meta.url,
});

const VOID_TAGS = new Set(["input", "br", "img", "hr", "meta", "link", "source"]);

function dataKey(name) {
  return name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

function decode(text) {
  return text
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'");
}

class El {
  constructor(tag) {
    this.tag = String(tag || "div").toLowerCase();
    this.id = "";
    this.children = [];
    this.parent = null;
    this.classes = new Set();
    this.attrs = {};
    this.dataset = {};
    this.listeners = {};
    this.style = {};
    this.text = "";
    this._html = "";
    this._text = null;
    this.hidden = false;
    this.disabled = false;
    this._value = "";
    this.scrollTop = 0;
    this.clientHeight = 0;
    this.scrollHeight = 0;
    this.classList = {
      add: (...names) => {
        for (const name of names) if (name) this.classes.add(name);
      },
      remove: (...names) => {
        for (const name of names) this.classes.delete(name);
      },
      toggle: (name, force) => {
        const on = force === undefined ? !this.classes.has(name) : !!force;
        if (on) this.classes.add(name);
        else this.classes.delete(name);
        return on;
      },
      contains: (name) => this.classes.has(name),
    };
  }

  get value() {
    return this._value;
  }

  set value(next) {
    this._value = String(next ?? "");
  }

  get innerHTML() {
    return this._html;
  }

  set innerHTML(html) {
    parseInto(this, String(html ?? ""));
  }

  get textContent() {
    if (this._text != null && this.children.length === 0) return this._text;
    return [this.text, ...this.children.map((child) => child.textContent)].join("");
  }

  set textContent(value) {
    this._text = String(value ?? "");
    this.text = this._text;
    this.children = [];
    this._html = this._text;
  }

  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === "id") this.id = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }

  removeAttribute(name) {
    delete this.attrs[name];
  }

  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }

  appendChild(child) {
    child.parent = this;
    this.children.push(child);
    return child;
  }

  matches(sel) {
    if (sel.startsWith("#")) return this.id === sel.slice(1);
    const attr = sel.match(/^([a-zA-Z0-9-]*)\[([^\]]+)\]$/);
    if (attr) {
      if (attr[1] && this.tag !== attr[1]) return false;
      const body = attr[2];
      if (body.startsWith("data-")) return this.dataset[dataKey(body)] != null;
      return this.getAttribute(body) != null;
    }
    if (sel.startsWith(".")) return this.classes.has(sel.slice(1));
    return this.tag === sel;
  }

  closest(sel) {
    let node = this;
    while (node) {
      if (node.matches?.(sel)) return node;
      node = node.parent;
    }
    return null;
  }

  querySelectorAll(sel) {
    const out = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (child.matches(sel)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }

  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }

  getElementById(id) {
    if (this.id === id) return this;
    for (const child of this.children) {
      const hit = child.getElementById(id);
      if (hit) return hit;
    }
    return null;
  }

  dispatch(type) {
    const event = {
      target: this,
      preventDefault() {},
    };
    const pending = [];
    let node = this;
    while (node) {
      for (const fn of node.listeners[type] || []) {
        const ret = fn(event);
        if (ret && typeof ret.then === "function") pending.push(ret);
      }
      node = type === "click" ? node.parent : null;
    }
    return Promise.all(pending);
  }

  click() {
    return this.dispatch("click");
  }

  getBoundingClientRect() {
    return { left: 0, right: 0, top: 0, bottom: 0, width: 800, height: 600 };
  }

  scrollIntoView() {}
}

function applyAttrs(el, raw) {
  const re = /([:@\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+)))?/g;
  let match;
  while ((match = re.exec(raw))) {
    const name = match[1];
    const value = match[2] ?? match[3] ?? match[4] ?? "";
    if (name === "id") el.id = value;
    else if (name === "class") {
      for (const part of value.split(/\s+/)) if (part) el.classes.add(part);
    } else if (name.startsWith("data-")) el.dataset[dataKey(name)] = value;
    else if (name === "value") el._value = value;
    else if (name === "hidden") el.hidden = true;
    else if (name === "disabled") el.disabled = true;
    if (match[2] != null || match[3] != null || match[4] != null || name === "hidden" || name === "disabled") {
      el.attrs[name] = value;
    }
  }
}

function parseInto(el, html) {
  el._html = html;
  el._text = null;
  el.children = [];
  el.text = "";
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z0-9]+)>|<([a-zA-Z0-9]+)([^>]*?)(\/?)>|([^<]+)/g;
  const stack = [el];
  let match;
  while ((match = re.exec(html))) {
    if (match[0].startsWith("<!--")) continue;
    if (match[1]) {
      const tag = match[1].toLowerCase();
      if (stack.length > 1 && stack[stack.length - 1].tag === tag) stack.pop();
      continue;
    }
    if (match[2]) {
      const child = new El(match[2]);
      applyAttrs(child, match[3] || "");
      child.parent = stack[stack.length - 1];
      stack[stack.length - 1].children.push(child);
      const selfClose = match[4] === "/" || match[0].endsWith("/>") || VOID_TAGS.has(child.tag);
      if (!selfClose) stack.push(child);
      continue;
    }
    if (match[5]) stack[stack.length - 1].text += decode(match[5]);
  }
}

function installDom() {
  const document = new El("document");
  document.createElement = (tag) => new El(tag);
  document.body = document.createElement("body");
  document.appendChild(document.body);
  globalThis.document = document;
  globalThis.window = globalThis;
  const location = { search: "", hash: "#/drives", href: "http://disknot.local/" };
  try {
    globalThis.location = location;
  } catch {
    // Node's location is a getter. Views only read it if the real api.js loads.
  }
  return document;
}

function htmlOf(id) {
  const el = document.getElementById(id);
  return el ? el.innerHTML : null;
}

function textOf(id) {
  const el = document.getElementById(id);
  return el ? el.textContent : null;
}

async function settle() {
  for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

async function click(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  await el.click();
  await settle();
}

function snapshot(root) {
  const banner = document.getElementById("priv-banner");
  const restart = document.getElementById("priv-restart");
  const refresh = document.getElementById("dr-refresh");
  return {
    root: root ? root.innerHTML : null,
    list: htmlOf("dr-list"),
    chip: htmlOf("dr-chip"),
    inspect: htmlOf("dr-inspect"),
    form: htmlOf("ch-form") || htmlOf("sn-form"),
    preflight: htmlOf("ch-preflight"),
    edu: htmlOf("ch-edu"),
    plan: htmlOf("sn-plan"),
    safety: htmlOf("sn-safety"),
    log: htmlOf("sn-log"),
    banner: banner ? banner.innerHTML : null,
    bannerHidden: banner ? !!banner.hidden : null,
    err: textOf("priv-err"),
    refreshDisabled: refresh ? refresh.getAttribute("aria-disabled") : null,
    restartDisabled: restart ? restart.getAttribute("aria-disabled") : null,
  };
}

async function renderView(name) {
  const shell = await import("../frontend/js/shell.js");
  const fixture = JSON.parse(process.env.DISKNOT_UI_FIXTURE || "{}");
  if (fixture.state) Object.assign(shell.state, fixture.state);
  const view = document.createElement("div");
  view.id = "view";
  document.body.appendChild(view);
  const mod = await import(`../frontend/js/views/${name}.js`);
  await mod.render(view);
  await settle();
  return view;
}

async function main() {
  installDom();
  const scenario = process.env.DISKNOT_UI_SCENARIO || "";
  const { calls } = await import("./frontend_stub_api.mjs");

  if (scenario === "copy") {
    const edu = await import("../frontend/js/content.js");
    const nvme = {
      path: "/dev/nvme1n1",
      kname: "nvme1n1",
      model: "WD SN770",
      size_bytes: 1099511627776,
      is_nvme: true,
      os_disk: false,
      swap: false,
      mounted: false,
      sanitize: { supported: true, block_erase: true, crypto_erase: false, overwrite: false },
    };
    process.stdout.write(JSON.stringify({
      drivesLede: edu.copy.drivesLede,
      chaffLede: edu.copy.chaffLede,
      sanitizeLede: edu.copy.sanitizeLede,
      driveIs: edu.driveIs(nvme),
      roleOs: edu.driveRole({ os_disk: true }),
      roleSwap: edu.driveRole({ swap: true }),
      roleMounted: edu.driveRole({ mounted: true }),
      roleFree: edu.driveRole({}),
      sanitize: edu.sanitizeDoes(nvme),
      sanitizeBlind: edu.sanitizeDoes({ is_nvme: true, sanitize: null }),
      notNvme: edu.sanitizeDoes({ is_nvme: false }),
      modes: edu.MODE_DOES,
      profiles: edu.PROFILE_DOES,
      layouts: edu.LAYOUT_DOES,
      completions: edu.COMPLETION_DOES,
      plans: edu.PLAN_DOES,
      seed: edu.seedDeterminism,
      reserve: edu.reserveMeaning,
      disclaimer: edu.chaffDisclaimer,
      arm: edu.armDestroy("/dev/nvme1n1"),
      bytes: edu.fmtBytes(1099511627776),
      afterZero: edu.afterWipeIs(
        { ok: true, cancelled: false },
        { passes: [{ kind: "zeros" }] },
      ),
      afterFail: edu.afterWipeIs({ ok: false, cancelled: false }, { passes: [] }),
      partitionMounted: edu.partitionIs({
        name: "nvme1n1p1", fstype: "ext4", mountpoints: ["/mnt/data"],
      }),
      partitionIdle: edu.partitionIs({ name: "nvme1n1p2", fstype: "ext4", mountpoints: [] }),
    }));
    return;
  }

  if (scenario === "drives" || scenario === "drives-inspect") {
    const root = await renderView("drives");
    if (scenario === "drives-inspect") await click("dr-in-nvme1n1");
    process.stdout.write(JSON.stringify({ ...snapshot(root), calls }));
    return;
  }

  if (
    scenario === "chaff" ||
    scenario === "chaff-preflight" ||
    scenario === "chaff-mode" ||
    scenario === "chaff-start"
  ) {
    const root = await renderView("chaff");
    if (scenario === "chaff-mode") {
      const button = [...document.getElementById("ch-mode").querySelectorAll("button")]
        .find((el) => el.dataset.mode === "percent_free");
      if (!button) throw new Error("percent_free mode button missing");
      await button.click();
      await settle();
    }
    if (scenario === "chaff-preflight") await click("ch-preflight-btn");
    if (scenario === "chaff-start") await click("ch-start");
    await settle();
    process.stdout.write(JSON.stringify({ ...snapshot(root), calls }));
    return;
  }

  if (scenario === "privilege" || scenario === "privilege-click") {
    const banner = document.createElement("div");
    banner.id = "priv-banner";
    document.body.appendChild(banner);
    const shell = await import("../frontend/js/shell.js");
    const { api } = await import("./frontend_stub_api.mjs");
    await shell.initShell(api);
    await settle();
    if (scenario === "privilege-click") await click("priv-restart");
    process.stdout.write(JSON.stringify({ ...snapshot(null), calls }));
    return;
  }

  if (scenario === "sanitize" || scenario === "sanitize-start") {
    const root = await renderView("sanitize");
    if (scenario === "sanitize-start") await click("sn-confirm-go");
    process.stdout.write(JSON.stringify({ ...snapshot(root), calls }));
    return;
  }

  throw new Error(`unknown scenario ${scenario}`);
}

try {
  await main();
} catch (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}
