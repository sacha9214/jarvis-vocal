// Extension VS Code de Jarvis : se relie au pont local de Jarvis (WebSocket, jeton lu sur le disque) et
// exécute ses demandes dans l'éditeur. Aucune dépendance, aucun build : VS Code charge ce fichier tel quel.
const vscode = require("vscode");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const RECONNECT_MS = 5000;
const COMMANDS = {
  save: "workbench.action.files.save", save_all: "workbench.action.files.saveAll",
  format: "editor.action.formatDocument", organize_imports: "editor.action.organizeImports",
  comment: "editor.action.commentLine", undo: "undo", redo: "redo", select_all: "editor.action.selectAll",
  find: "actions.find", replace: "editor.action.startFindReplaceAction", rename: "editor.action.rename",
  quick_fix: "editor.action.quickFix", definition: "editor.action.revealDefinition",
  references: "editor.action.goToReferences", symbol: "workbench.action.gotoSymbol",
  back: "workbench.action.navigateBack", forward: "workbench.action.navigateForward",
  next_error: "editor.action.marker.nextInFiles", previous_error: "editor.action.marker.prevInFiles",
  fold: "editor.foldAll", unfold: "editor.unfoldAll", word_wrap: "editor.action.toggleWordWrap",
  terminal: "workbench.action.terminal.toggleTerminal", clear_terminal: "workbench.action.terminal.clear",
  problems: "workbench.actions.view.problems", explorer: "workbench.view.explorer",
  source_control: "workbench.view.scm", search_view: "workbench.view.search",
  sidebar: "workbench.action.toggleSidebarVisibility", zen: "workbench.action.toggleZenMode",
  palette: "workbench.action.showCommands", quick_open: "workbench.action.quickOpen",
  split: "workbench.action.splitEditor", new_file: "workbench.action.files.newUntitledFile",
  close_tab: "workbench.action.closeActiveEditor", next_tab: "workbench.action.nextEditor",
  previous_tab: "workbench.action.previousEditor", reopen_tab: "workbench.action.reopenClosedEditor",
};
const RUNS = { run: "workbench.action.debug.run", debug: "workbench.action.debug.start", test: "testing.runAll" };
const EXCLUDE = "{**/node_modules,**/.git,**/.venv,**/venv,**/__pycache__,**/dist,**/build,**/.next,**/target}/**";

let socket = null;
let statusBar = null;
let timer = null;

function dataDir() {
  const configured = vscode.workspace.getConfiguration("jarvis").get("dataDir");
  if (configured) return configured;
  if (process.env.JARVIS_HOME) return process.env.JARVIS_HOME;
  const home = os.homedir();
  if (process.platform === "win32") {
    return path.join(process.env.LOCALAPPDATA || path.join(home, "AppData", "Local"), "jarvis");
  }
  if (process.platform === "darwin") return path.join(home, "Library", "Application Support", "jarvis");
  return path.join(process.env.XDG_DATA_HOME || path.join(home, ".local", "share"), "jarvis");
}

function readToken() {
  try {
    return fs.readFileSync(path.join(dataDir(), "bridge-token"), "utf-8").trim();
  } catch {
    return "";
  }
}

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function showState(linked, detail) {
  if (!statusBar) return;
  statusBar.text = linked ? "$(broadcast) Jarvis" : "$(debug-disconnect) Jarvis";
  statusBar.tooltip = linked ? "Relié à Jarvis" : `Jarvis hors ligne : ${detail || "lance Jarvis"} (clic : réessayer)`;
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  if (typeof WebSocket !== "function") {
    showState(false, "cette version de l'éditeur n'a pas de WebSocket");
    return;
  }
  const token = readToken();
  if (!token) {
    showState(false, `jeton introuvable dans ${dataDir()} : lance Jarvis une première fois`);
    return;
  }
  const port = vscode.workspace.getConfiguration("jarvis").get("port") || 47831;
  try {
    socket = new WebSocket(`ws://127.0.0.1:${port}/editor`);
  } catch {
    return;
  }
  socket.onopen = () => {
    send({
      type: "hello", token, app: vscode.env.appName, focused: vscode.window.state.focused,
      version: vscode.version, workspace: vscode.workspace.name || "",
    });
    showState(true);
  };
  socket.onmessage = async (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.id === undefined) return;
    try {
      send({ id: message.id, ok: true, result: await handle(message.action, message.params || {}) });
    } catch (error) {
      send({ id: message.id, ok: false, error: String((error && error.message) || error) });
    }
  };
  socket.onclose = () => { socket = null; showState(false); };
  socket.onerror = () => { if (socket) socket.close(); };
}

// -- lecture

function activeEditor() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) throw new Error("Aucun fichier n'est ouvert dans l'éditeur.");
  return editor;
}

function filePath(document) {
  return document.uri.scheme === "file" ? document.uri.fsPath : null;
}

function relative(uri) {
  return vscode.workspace.asRelativePath(uri, false);
}

function status() {
  const editor = vscode.window.activeTextEditor;
  const folders = (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath);
  return {
    app: vscode.env.appName, workspace: folders[0] || null, workspaces: folders,
    file: editor ? filePath(editor.document) : null, language: editor ? editor.document.languageId : null,
    line: editor ? editor.selection.active.line + 1 : null, dirty: editor ? editor.document.isDirty : false,
    selection: editor ? !editor.selection.isEmpty : false,
  };
}

function numbered(document, from, to) {
  const lines = [];
  for (let i = from; i < to; i += 1) lines.push(`${i + 1}| ${document.lineAt(i).text}`);
  return lines.join("\n");
}

function readFile(params) {
  const editor = activeEditor();
  const document = editor.document;
  const maxChars = Number(params.max_chars) || 6000;
  const total = document.lineCount;
  const cursor = editor.selection.active.line;
  let from = 0;
  let to = total;
  if (document.getText().length > maxChars) {
    // Fenêtre autour du curseur : on l'étend ligne à ligne, en alternant au-dessus et en dessous.
    from = cursor;
    to = cursor + 1;
    let size = document.lineAt(cursor).text.length;
    let up = true;
    while (size < maxChars && (from > 0 || to < total)) {
      if (up && from > 0) { from -= 1; size += document.lineAt(from).text.length + 6; }
      else if (to < total) { to += 1; size += document.lineAt(to - 1).text.length + 6; }
      up = !up;
    }
  }
  return {
    file: filePath(document), name: relative(document.uri), language: document.languageId, line: cursor + 1,
    total_lines: total, from: from + 1, to, text: numbered(document, from, to),
    selection: document.getText(editor.selection).slice(0, 2000), dirty: document.isDirty,
  };
}

function readSelection() {
  const editor = activeEditor();
  const selection = editor.selection;
  return {
    file: filePath(editor.document), name: relative(editor.document.uri), from: selection.start.line + 1,
    to: selection.end.line + 1, text: editor.document.getText(selection).slice(0, 8000),
  };
}

function allTabs() {
  const tabs = [];
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const uri = tab.input && tab.input.uri;
      tabs.push({
        index: tabs.length + 1, label: tab.label, file: uri ? relative(uri) : null, uri, active: tab.isActive,
        dirty: tab.isDirty, group: group.viewColumn,
      });
    }
  }
  return tabs;
}

function readTabs() {
  return { tabs: allTabs().map(({ uri, ...tab }) => tab) };
}

function errors(params) {
  const editor = vscode.window.activeTextEditor;
  const current = params.scope === "file" ? (editor && editor.document.uri.toString()) : null;
  if (params.scope === "file" && !current) throw new Error("Aucun fichier n'est ouvert dans l'éditeur.");
  const items = [];
  let counted = { erreur: 0, avertissement: 0 };
  for (const [uri, diagnostics] of vscode.languages.getDiagnostics()) {
    if (current && uri.toString() !== current) continue;
    for (const diagnostic of diagnostics) {
      if (diagnostic.severity > vscode.DiagnosticSeverity.Warning) continue;
      const severity = diagnostic.severity === vscode.DiagnosticSeverity.Error ? "erreur" : "avertissement";
      counted[severity] += 1;
      items.push({
        file: relative(uri), line: diagnostic.range.start.line + 1, severity,
        message: String(diagnostic.message).replace(/\s+/g, " ").slice(0, 300), source: diagnostic.source || "",
      });
    }
  }
  const order = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  items.sort((a, b) => (a.severity === b.severity ? order(a.file, b.file) || a.line - b.line
    : a.severity === "erreur" ? -1 : 1));
  return { errors: counted.erreur, warnings: counted.avertissement, items: items.slice(0, Number(params.max) || 40) };
}

// -- actions

function rankFiles(uris, wanted) {
  const base = wanted.toLowerCase();
  const score = (uri) => {
    const name = path.basename(uri.fsPath).toLowerCase();
    return (name === base ? 0 : name.startsWith(base) ? 1 : 2) * 1000 + uri.fsPath.length;
  };
  return [...uris].sort((a, b) => score(a) - score(b));
}

// « fake browser py » dit à la voix : fake browser.py, fake_browser.py, fake-browser.py, fakebrowser.py.
function spokenNames(base) {
  let stem = base;
  let ext = "";
  const dot = base.lastIndexOf(".");
  const spoken = base.match(/^(.+) ([a-z0-9]{1,5})$/i);
  if (dot > 0) [stem, ext] = [base.slice(0, dot), base.slice(dot)];
  else if (spoken) [stem, ext] = [spoken[1], `.${spoken[2]}`];
  const stems = [stem, stem.replace(/ /g, "_"), stem.replace(/ /g, "-"), stem.replace(/ /g, "")];
  return [...new Set([base, ...stems.map((s) => s + ext)])];
}

async function open(params) {
  let editor = vscode.window.activeTextEditor;
  if (params.tab) {
    const tab = allTabs()[Number(params.tab) - 1];
    if (!tab) throw new Error("Cet onglet n'existe pas.");
    if (!tab.uri) throw new Error(`L'onglet ${tab.label} n'est pas un fichier.`);
    editor = await vscode.window.showTextDocument(tab.uri, { viewColumn: tab.group });
  } else if (params.file) {
    const wanted = String(params.file).trim();
    let uri = null;
    if (path.isAbsolute(wanted) && fs.existsSync(wanted)) uri = vscode.Uri.file(wanted);
    else {
      for (const candidate of spokenNames(path.basename(wanted))) {
        const found = await vscode.workspace.findFiles(`**/*${candidate}*`, EXCLUDE, 50);
        if (!found.length) continue;
        const matching = found.filter((f) => f.fsPath.toLowerCase().includes(wanted.toLowerCase()));
        uri = rankFiles(matching.length ? matching : found, candidate)[0] || null;
        break;
      }
    }
    if (!uri) throw new Error(`Je ne trouve pas de fichier ${wanted} dans le projet.`);
    editor = await vscode.window.showTextDocument(uri, { preview: false });
  }
  if (!editor) throw new Error("Aucun fichier n'est ouvert dans l'éditeur.");
  if (params.line) {
    const line = Math.max(0, Math.min(editor.document.lineCount - 1, Number(params.line) - 1));
    const position = new vscode.Position(line, 0);
    editor.selection = new vscode.Selection(position, position);
    editor.revealRange(new vscode.Range(position, position), vscode.TextEditorRevealType.InCenter);
  }
  return { file: filePath(editor.document), name: relative(editor.document.uri), line: editor.selection.active.line + 1 };
}

async function command(params) {
  const id = COMMANDS[params.action];
  if (!id) throw new Error(`Action inconnue : ${params.action}`);
  await vscode.commands.executeCommand(id);
  return { done: true, command: id };
}

async function run(params) {
  const id = RUNS[params.mode];
  if (!id) throw new Error(`Mode inconnu : ${params.mode}`);
  await vscode.commands.executeCommand(id);
  return { done: true, command: id };
}

async function search(params) {
  const query = String(params.query || "").trim();
  if (!query) throw new Error("Dis-moi quoi chercher.");
  await vscode.commands.executeCommand("workbench.action.findInFiles", { query, triggerSearch: true });
  return { done: true };
}

async function insert(params) {
  const editor = activeEditor();
  const text = String(params.text || "");
  const mode = params.mode || "cursor";
  const ok = await editor.edit((builder) => {
    if (mode === "replace") builder.replace(editor.selection, text);
    else if (mode === "line") {
      const line = editor.document.lineAt(editor.selection.active.line);
      const indent = line.text.match(/^\s*/)[0];
      builder.insert(line.range.end, `\n${indent}${text}`);
    } else builder.insert(editor.selection.active, text);
  });
  if (!ok) throw new Error("L'éditeur a refusé la modification.");
  return { done: true, file: filePath(editor.document), name: relative(editor.document.uri) };
}

async function handle(action, params) {
  switch (action) {
    case "status": return status();
    case "read":
      if (params.what === "selection") return readSelection();
      if (params.what === "tabs") return readTabs();
      return readFile(params);
    case "errors": return errors(params);
    case "open": return open(params);
    case "command": return command(params);
    case "run": return run(params);
    case "search": return search(params);
    case "insert": return insert(params);
    default: throw new Error(`Action inconnue : ${action}`);
  }
}

// -- cycle de vie

function activate(context) {
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 50);
  statusBar.command = "jarvis.reconnect";
  statusBar.show();
  showState(false);
  context.subscriptions.push(
    statusBar,
    vscode.commands.registerCommand("jarvis.reconnect", connect),
    vscode.window.onDidChangeWindowState((state) => { send({ type: "focus", focused: state.focused }); connect(); }),
  );
  timer = setInterval(connect, RECONNECT_MS);
  connect();
}

function deactivate() {
  if (timer) clearInterval(timer);
  if (socket) socket.close();
}

module.exports = { activate, deactivate, COMMANDS, RUNS, handle, spokenNames };
