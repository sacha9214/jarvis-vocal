// Arrière-plan de l'extension Jarvis : se relie à Jarvis (WebSocket local, jeton) et exécute ses
// demandes dans l'onglet actif. Chrome, Edge, Brave, Arc, Opera ; Firefox via manifest.firefox.json.
if (typeof importScripts === "function") importScripts("config.js", "actions.js");

const api = globalThis.browser ?? globalThis.chrome;
const CONFIG = globalThis.JARVIS_CONFIG;
const BROWSER = detectBrowser();
let socket = null;
let focused = true;

function detectBrowser() {
  const ua = navigator.userAgent;
  if (ua.includes("Firefox/")) return "Firefox";
  if (ua.includes("Edg/")) return "Microsoft Edge";
  if (ua.includes("OPR/")) return "Opera";
  if (navigator.brave) return "Brave";
  return "Chrome";
}

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function setBadge(text) {
  try { api.action.setBadgeText({ text }); } catch { /* navigateur sans badge */ }
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  try {
    socket = new WebSocket(`ws://127.0.0.1:${CONFIG.port}/bridge`);
  } catch {
    return;
  }
  socket.onopen = async () => {
    send({ type: "hello", token: CONFIG.token, browser: BROWSER, focused, version: api.runtime.getManifest().version });
    setBadge((await hasHostPermission()) ? "" : "!");
  };
  socket.onmessage = async (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.id === undefined) return;
    try {
      send({ id: message.id, ok: true, result: await handle(message.action, message.params || {}) });
    } catch (error) {
      send({ id: message.id, ok: false, error: explain(error) });
    }
  };
  socket.onclose = () => { socket = null; setBadge("off"); };
  socket.onerror = () => { if (socket) socket.close(); };
}

const NO_PERMISSION = "Jarvis n'a pas encore le droit de lire les pages de ce navigateur : clique sur l'icône Jarvis "
  + "dans la barre d'outils et accepte l'accès à tous les sites.";

async function hasHostPermission() {
  try { return await api.permissions.contains({ origins: ["<all_urls>"] }); } catch { return true; }
}

function explain(error) {
  const text = String((error && error.message) || error);
  if (/Missing host permission/i.test(text)) return NO_PERMISSION;
  if (/cannot be scripted|Cannot access|chrome:\/\/|edge:\/\/|about:/i.test(text)) {
    return "Cette page est protégée par le navigateur (page interne ou boutique d'extensions).";
  }
  return text;
}

async function activeTab() {
  const [tab] = await api.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error("Aucun onglet actif.");
  return tab;
}

async function inPage(tab, name, params, { world = "ISOLATED", allFrames = false } = {}) {
  const results = await api.scripting.executeScript({
    target: { tabId: tab.id, allFrames }, world, func: globalThis.JarvisActions[name], args: [params],
  });
  const failed = (results || []).find((r) => r && r.error);
  if (failed) throw new Error(String((failed.error && failed.error.message) || failed.error));   // Firefox
  const found = (results || []).map((r) => r && r.result).filter((r) => r !== undefined && r !== null);
  // Firefox : sans l'accès aux sites (facultatif en MV3), l'injection ne renvoie rien au lieu d'échouer.
  if (!found.length && !(await hasHostPermission())) throw new Error(NO_PERMISSION);
  return found;
}

async function handle(action, params) {
  const tab = await activeTab();
  switch (action) {
    case "status":
      return { title: tab.title, url: tab.url, browser: BROWSER };
    case "media": {
      const results = await inPage(tab, "media", params, { world: "MAIN", allFrames: true });
      return { ...(results.find((r) => r.found) || { found: false }), title: tab.title };
    }
    case "page": {
      const [result] = await inPage(tab, "page", params);
      return { ...result, title: tab.title, url: tab.url };
    }
    case "click":
    case "scroll":
    case "type": {
      const [result] = await inPage(tab, action, params);
      return result;
    }
    case "navigate":
      if (params.url) await api.tabs.update(tab.id, { url: params.url });
      else if (params.direction === "back") await api.tabs.goBack(tab.id);
      else if (params.direction === "forward") await api.tabs.goForward(tab.id);
      else await api.tabs.reload(tab.id);
      return { done: true };
    case "tabs": {
      const tabs = await api.tabs.query({ lastFocusedWindow: true });
      if (params.action === "switch") {
        const current = tabs.findIndex((t) => t.active);
        const target = params.index ? tabs[params.index - 1] : tabs[(current + 1) % tabs.length];
        if (!target) throw new Error("Cet onglet n'existe pas.");
        await api.tabs.update(target.id, { active: true });
        return { title: target.title };
      }
      if (params.action === "close") {
        await api.tabs.remove(tab.id);
        return { title: tab.title };
      }
      if (params.action === "new") {
        const created = await api.tabs.create(params.url ? { url: params.url } : {});
        return { title: created.title || "Nouvel onglet" };
      }
      return { tabs: tabs.map((t, i) => ({ index: i + 1, title: t.title, url: t.url, active: t.active })) };
    }
    default:
      throw new Error(`Action inconnue : ${action}`);
  }
}

// Un clic sur l'icône demande l'accès aux sites (Firefox ne l'accorde pas à l'installation) et relie Jarvis.
api.action.onClicked.addListener(async () => {
  try {
    if (await api.permissions.request({ origins: ["<all_urls>"] })) setBadge(socket ? "" : "off");
  } catch { /* navigateur sans demande de permission à la volée */ }
  connect();
});
api.alarms.create("jarvis-reconnexion", { periodInMinutes: 0.5 });
api.alarms.onAlarm.addListener(connect);
api.runtime.onStartup.addListener(connect);
api.runtime.onInstalled.addListener(connect);
api.tabs.onActivated.addListener(connect);
api.windows.onFocusChanged.addListener((windowId) => {
  focused = windowId !== api.windows.WINDOW_ID_NONE;
  send({ type: "focus", focused });
  connect();
});
setInterval(() => send({ type: "ping" }), 20000);   // garde le service d'arrière-plan éveillé (Chrome 116+)
connect();
