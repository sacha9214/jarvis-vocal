// Faux navigateur pour tester l'arrière-plan de l'extension : on charge le vrai background.js avec des API
// Chrome simulées, et on lui parle par l'entrée/sortie standard au lieu d'un WebSocket (le transport, lui,
// est testé côté Python avec l'origine et le jeton ; les injections dans la page le sont dans une vraie page).
// Usage : node fake_browser.js <dossier de l'extension> <fichier où noter les appels>
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const vm = require("node:vm");

const [folder, output] = process.argv.slice(2);
const calls = [];
const tabs = [
  { id: 1, title: "Vidéo test", url: "https://exemple.test/watch?v=1", active: true },
  { id: 2, title: "Deuxième onglet", url: "https://exemple.test/autre", active: false },
];
let current = null;

function record(name, details) {
  calls.push({ name, ...details });
  fs.writeFileSync(output, JSON.stringify(calls), "utf-8");
}

class StdioSocket {
  constructor() {
    this.readyState = StdioSocket.OPEN;
    current = this;
    setTimeout(() => this.onopen && this.onopen(), 0);
  }

  send(data) {
    process.stdout.write(data + "\n");
  }

  close() {
    this.readyState = StdioSocket.CLOSED;
    if (this.onclose) this.onclose();
  }
}
StdioSocket.CONNECTING = 0;
StdioSocket.OPEN = 1;
StdioSocket.CLOSED = 3;

const chrome = {
  action: { setBadgeText: (details) => record("setBadgeText", details), onClicked: { addListener: () => {} } },
  permissions: {
    contains: async () => process.env.FAKE_NO_PERMISSION !== "1",
    request: async () => true,
  },
  runtime: {
    getManifest: () => ({ version: "1.0" }),
    onStartup: { addListener: () => {} },
    onInstalled: { addListener: () => {} },
  },
  alarms: { create: (name, info) => record("alarm", { alarm: name, info }), onAlarm: { addListener: () => {} } },
  tabs: {
    query: async () => tabs,
    update: async (id, props) => { record("update", { id, props }); return tabs.find((tab) => tab.id === id); },
    goBack: async (id) => record("goBack", { id }),
    goForward: async (id) => record("goForward", { id }),
    reload: async (id) => record("reload", { id }),
    remove: async (id) => record("remove", { id }),
    create: async (props) => { record("create", props); return { id: 3, title: "Nouvel onglet" }; },
    onActivated: { addListener: () => {} },
  },
  windows: { WINDOW_ID_NONE: -1, onFocusChanged: { addListener: () => {} } },
  scripting: {
    executeScript: async ({ target, world, func, args }) => {
      record("executeScript", { tabId: target.tabId, allFrames: Boolean(target.allFrames), world, params: args[0],
                                action: typeof func === "function" ? func.name : String(func) });
      if (args[0] && args[0].text === "interdit") {
        throw new Error("Cannot access contents of the page at chrome://extensions/");
      }
      if (process.env.FAKE_NO_PERMISSION === "1") return [];        // Firefox sans accès aux sites : rien

      return [{ result: { found: true, volume: 42, clicked: true } }];
    },
  },
};

const context = vm.createContext({
  WebSocket: StdioSocket, console, setInterval, clearInterval, setTimeout, clearTimeout, JSON, URL,
  navigator: { userAgent: "Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36" },
  chrome,
});
context.globalThis = context;
for (const file of ["config.js", "actions.js", "background.js"]) {
  vm.runInContext(fs.readFileSync(path.join(folder, file), "utf-8"), context, { filename: file });
}

readline.createInterface({ input: process.stdin }).on("line", (line) => {
  if (line.trim() && current && current.onmessage) current.onmessage({ data: line });
});
setTimeout(() => process.exit(0), 60000);     // filet de sécurité si le test s'arrête avant
