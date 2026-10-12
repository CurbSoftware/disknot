// In-process stand-in for the Qt bridge. Fixture JSON arrives in
// DISKNOT_UI_FIXTURE. Views import this module through frontend_loader.mjs.

const fixture = JSON.parse(process.env.DISKNOT_UI_FIXTURE || "{}");
export const calls = [];

const listeners = new Map();

function answer(name, fallback) {
  return async (...args) => {
    calls.push(name);
    if (Object.prototype.hasOwnProperty.call(fixture, name)) return fixture[name];
    return fallback;
  };
}

export const api = {
  on(signal, fn) {
    if (!listeners.has(signal)) listeners.set(signal, []);
    listeners.get(signal).push(fn);
    return () => {};
  },
  getBootstrap: answer("getBootstrap", {
    core: { ready: true, version: "0.1.0" },
    tools: { lsblk: "/usr/bin/lsblk", pkexec: "/usr/bin/pkexec" },
    busy: false,
    kind: null,
    root: false,
    app_settings: {},
  }),
  deviceList: answer("deviceList", { devices: [], error: "Disk scan returned nothing." }),
  deviceDetail: answer("deviceDetail", { error: "no device" }),
  relaunchAsRoot: answer("relaunchAsRoot", {
    ok: false,
    error: "pkexec exited 126 with no message",
  }),
  wipePlan: answer("wipePlan", { error: "no device" }),
  wipeStart: answer("wipeStart", { ok: false, error: "confirmation mismatch" }),
  wipeAbort: answer("wipeAbort", { ok: true }),
  chaffPreflight: answer("chaffPreflight", { ok: false, error: "preflight failed" }),
  chaffStart: answer("chaffStart", { ok: false, error: "start failed" }),
  chaffCancel: answer("chaffCancel", { ok: true }),
  chaffPause: answer("chaffPause", { ok: true }),
  chaffResume: answer("chaffResume", { ok: true }),
  pickDirectory: answer("pickDirectory", ""),
};
