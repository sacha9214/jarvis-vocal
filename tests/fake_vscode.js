// Faux VS Code pour tester extension.js pour de vrai sous Node : le module « vscode » est simulé, le WebSocket
// remplacé par l'entrée/sortie standard (le transport est testé côté Python, avec la règle d'origine).
// Usage : node fake_vscode.js <dossier de l'extension> <fichier où noter les appels> <dossier du jeton>
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const readline = require("node:readline");

const [folder, output, dataDir] = process.argv.slice(2);
const calls = [];
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

  send(data) { process.stdout.write(data + "\n"); }

  close() { this.readyState = StdioSocket.CLOSED; if (this.onclose) this.onclose(); }
}
StdioSocket.CONNECTING = 0;
StdioSocket.OPEN = 1;
StdioSocket.CLOSED = 3;
globalThis.WebSocket = StdioSocket;

// -- modèle d'éditeur minimal

class Position {
  constructor(line, character) { this.line = line; this.character = character; }
}
class Range {
  constructor(start, end) { this.start = start; this.end = end; }
}
class Selection extends Range {
  get active() { return this.end; }

  get isEmpty() { return this.start.line === this.end.line && this.start.character === this.end.character; }
}
class Uri {
  constructor(fsPath) { this.fsPath = fsPath; this.scheme = "file"; }

  toString() { return `file://${this.fsPath}`; }

  static file(fsPath) { return new Uri(fsPath); }
}

const ROOT = "/projet";
const FILES = {
  "/projet/src/app.py": "import os\n\n\ndef main():\n    total = add(1, 2)\n    print(total)\n\n\nmain()\n",
  "/projet/src/app_test.py": "def test_add():\n    assert add(1, 2) == 3\n",
  "/projet/lib/app.py": "# doublon\n",
  "/projet/README.md": "# Projet\n",
};

function makeDocument(fsPath) {
  const text = FILES[fsPath];
  const lines = text.split("\n");
  const offset = (position) => lines.slice(0, position.line).reduce((n, l) => n + l.length + 1, 0) + position.character;
  return {
    uri: Uri.file(fsPath), languageId: fsPath.endsWith(".py") ? "python" : "markdown", isDirty: false,
    lineCount: lines.length,
    getText: (range) => (range ? text.slice(offset(range.start), offset(range.end)) : text),
    lineAt: (i) => ({ text: lines[i], range: new Range(new Position(i, 0), new Position(i, lines[i].length)) }),
  };
}

function makeEditor(fsPath, line = 0) {
  const editor = {
    document: makeDocument(fsPath), selection: new Selection(new Position(line, 0), new Position(line, 0)),
    revealRange: (range, kind) => record("revealRange", { line: range.start.line, kind }),
    edit: async (callback) => {
      callback({
        insert: (position, text) => record("insert", { line: position.line, character: position.character, text }),
        replace: (range, text) => record("replace", { from: range.start.line, to: range.end.line, text }),
      });
      return true;
    },
  };
  return editor;
}

const state = { editor: makeEditor("/projet/src/app.py", 4), focused: true };
state.editor.selection = new Selection(new Position(3, 0), new Position(5, 16));   // def main … print(total)

const vscode = {
  Position, Range, Selection, Uri,
  DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
  StatusBarAlignment: { Left: 1, Right: 2 },
  TextEditorRevealType: { Default: 0, InCenter: 2 },
  version: "1.134.0",
  env: { appName: "Visual Studio Code" },
  window: {
    get activeTextEditor() { return state.editor; },
    get state() { return { focused: state.focused }; },
    tabGroups: { all: [{ viewColumn: 1, tabs: [
      { label: "app.py", input: { uri: Uri.file("/projet/src/app.py") }, isActive: true, isDirty: false },
      { label: "README.md", input: { uri: Uri.file("/projet/README.md") }, isActive: false, isDirty: true },
      { label: "Réglages", input: {}, isActive: false, isDirty: false },
    ] }] },
    createStatusBarItem: () => ({ show: () => record("statusBar", {}), text: "", tooltip: "" }),
    onDidChangeWindowState: (listener) => { state.onFocus = listener; return { dispose: () => {} }; },
    showTextDocument: async (uri, options) => {
      record("showTextDocument", { path: uri.fsPath, options });
      state.editor = makeEditor(uri.fsPath);
      return state.editor;
    },
  },
  workspace: {
    name: "projet",
    workspaceFolders: [{ uri: Uri.file(ROOT) }],
    getConfiguration: () => ({ get: (key) => ({ port: 47831, dataDir }[key]) }),
    asRelativePath: (uri) => path.relative(ROOT, uri.fsPath),
    findFiles: async (glob, exclude, max) => {
      record("findFiles", { glob, exclude, max });
      const base = glob.replace("**/*", "").replace(/\*$/, "").toLowerCase();
      return Object.keys(FILES).filter((f) => path.basename(f).toLowerCase().includes(base)).map((f) => Uri.file(f));
    },
  },
  languages: {
    getDiagnostics: () => [
      [Uri.file("/projet/src/app_test.py"), [
        { severity: 1, range: new Range(new Position(1, 0), new Position(1, 5)), message: "ligne trop longue", source: "ruff" },
      ]],
      [Uri.file("/projet/src/app.py"), [
        { severity: 3, range: new Range(new Position(0, 0), new Position(0, 1)), message: "indice", source: "" },
        { severity: 0, range: new Range(new Position(4, 12), new Position(4, 15)), message: "« add » n'est pas défini", source: "Pylance" },
        { severity: 1, range: new Range(new Position(0, 0), new Position(0, 9)), message: "import inutilisé", source: "ruff" },
      ]],
    ],
  },
  commands: {
    executeCommand: async (id, args) => record("executeCommand", { id, args: args || null }),
    registerCommand: (id, callback) => { record("registerCommand", { id }); state.reconnect = callback; return { dispose: () => {} }; },
  },
};

const load = Module._load;
Module._load = function patched(request, ...rest) {
  return request === "vscode" ? vscode : load.call(this, request, ...rest);
};

const extension = require(path.join(folder, "extension.js"));
extension.activate({ subscriptions: [] });

readline.createInterface({ input: process.stdin }).on("line", (line) => {
  if (!line.trim()) return;
  if (line === "@blur") { state.focused = false; state.onFocus({ focused: false }); return; }
  if (current && current.onmessage) current.onmessage({ data: line });
});
setTimeout(() => process.exit(0), 60000);     // filet de sécurité si le test s'arrête avant
