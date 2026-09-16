(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const TAU = Math.PI * 2;
  const DEMO = new URLSearchParams(location.search).has("demo");

  const STATES = {
    offline:   { label: "HORS LIGNE",   title: "HORS LIGNE",  sub: "RECONNEXION…",        hue: 205, sat: 22,  light: 58, energy: 0.15, spin: 0.15 },
    sleeping:  { label: "EN VEILLE",    title: "EN VEILLE",   sub: "DIS « HEY JARVIS »",  hue: 194, sat: 96,  light: 62, energy: 0.38, spin: 0.35 },
    listening: { label: "ÉCOUTE",       title: "ÉCOUTE",      sub: "JE T'ÉCOUTE",         hue: 190, sat: 100, light: 66, energy: 0.95, spin: 0.9 },
    thinking:  { label: "RÉFLEXION",    title: "ANALYSE",     sub: "TRAITEMENT EN COURS", hue: 38,  sat: 100, light: 60, energy: 0.8,  spin: 2.6 },
    speaking:  { label: "PAROLE",       title: "RÉPONSE",     sub: "SYNTHÈSE VOCALE",     hue: 192, sat: 92,  light: 76, energy: 1,    spin: 1.1 },
    confirm:   { label: "CONFIRMATION", title: "AUTORISATION", sub: "OUI OU NON ?",       hue: 18,  sat: 100, light: 60, energy: 0.9,  spin: 0.5 },
  };
  const TOOL_LABELS = {
    open_app: "Ouverture d'application", open_website: "Ouverture de site", web_search: "Recherche web",
    open_folder: "Ouverture de dossier", media: "Lecture", set_volume: "Volume", set_timer: "Minuteur",
    cancel_timer: "Annulation de minuteur", system_status: "État du système", close_app: "Fermeture d'application",
    lock_screen: "Verrouillage", power: "Alimentation", cancel_power: "Annulation d'extinction",
    describe_screen: "Regard sur l'écran", browser_media: "Vidéo du navigateur", browser_read: "Lecture de la page",
    browser_open: "Clic dans la page", browser_scroll: "Défilement", browser_navigate: "Navigation",
    browser_search: "Recherche dans le navigateur", browser_tabs: "Onglets", browser_close_tab: "Fermeture d'onglet",
    browser_type: "Saisie dans la page", review_code: "Review de code", review_control: "Suivi de la review",
    app_read: "Lecture de l'application", app_press: "Clic dans l'application", app_type: "Saisie dans l'application",
    app_shortcut: "Raccourci clavier", code_read: "Lecture du code", code_errors: "Erreurs de l'éditeur",
    code_open: "Ouverture dans l'éditeur", code_command: "Commande de l'éditeur", code_search: "Recherche dans le projet",
    code_insert: "Écriture dans le fichier", code_run: "Exécution dans l'éditeur",
  };
  const MARKS = [["stt", "Transcription"], ["fastpath", "Réflexe"], ["llm_first_token", "1er token"],
                 ["first_chunk", "1re phrase"], ["tool", "Action"], ["first_audio", "1er son"]];

  const store = {
    state: "offline", mic: 0, out: 0, engine: { active: "local", model: "" },
    data: null, dirty: new Map(), tab: null, confirmTimer: null, confirmTimeout: 6,
    cpuHistory: [], timers: [],
  };

  // ---------------------------------------------------------------- utilitaires

  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const ease = (t) => 1 - Math.pow(1 - clamp(t, 0, 1), 3);
  const noise = (x) => (Math.sin(x * 1.7) + Math.sin(x * 2.9 + 1.3) + Math.sin(x * 4.3 + 2.1)) / 6 + 0.5;
  const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const now = () => new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const getPath = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

  async function api(path, body) {
    if (DEMO) return Demo.api(path, body);
    const options = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis": "1" }, body: JSON.stringify(body),
    };
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }

  function toast(message, kind = "ok", action) {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.innerHTML = `<div>${esc(message)}</div>`;
    if (action) {
      const button = document.createElement("button");
      button.className = "btn primary";
      button.textContent = action.label;
      button.onclick = () => { action.run(); dismiss(); };
      el.appendChild(button);
    }
    $("#toasts").appendChild(el);
    const dismiss = () => { el.classList.add("out"); setTimeout(() => el.remove(), 400); };
    setTimeout(dismiss, action ? 9000 : 4200);
  }

  // ---------------------------------------------------------------- réacteur

  class Reactor {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.t = 0;
      this.boot = 0;
      this.look = { ...STATES.offline };
      this.level = 0;
      this.rot = [0, 0, 0, 0, 0];
      this.particles = Array.from({ length: 70 }, () => ({
        a: Math.random() * TAU, r: 0.35 + Math.random() * 0.75, s: 0.15 + Math.random() * 0.6, z: Math.random(),
      }));
      new ResizeObserver(() => this.resize()).observe(canvas);
      this.resize();
    }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.max(1, rect.width * this.dpr);
      this.canvas.height = Math.max(1, rect.height * this.dpr);
    }

    static rgb(h, s, l) {
      const sat = s / 100;
      const light = l / 100;
      const a = sat * Math.min(light, 1 - light);
      const channel = (n) => {
        const k = (n + h / 30) % 12;
        return 255 * (light - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)));
      };
      return [channel(0), channel(8), channel(4)];
    }

    // Couleur interpolée en RVB : passer de l'ambre au cyan ne doit pas traverser le vert.
    color(alpha, lighter = 0) {
      const mix = clamp(lighter / 45, 0, 1);
      const [r, g, b] = this.rgb.map((c) => Math.round(c + (255 - c) * mix));
      return `rgba(${r}, ${g}, ${b}, ${clamp(alpha, 0, 1)})`;
    }

    step(dt, target, level) {
      const k = 1 - Math.exp(-dt * 3.2);
      const look = this.look;
      const goal = Reactor.rgb(target.hue, target.sat, target.light);
      this.rgb = this.rgb ? this.rgb.map((c, i) => lerp(c, goal[i], k)) : goal;
      let dh = target.hue - look.hue;
      if (dh > 180) dh -= 360;
      if (dh < -180) dh += 360;
      look.hue += dh * k;
      for (const key of ["sat", "light", "energy", "spin"]) look[key] = lerp(look[key], target[key], k);
      this.level = lerp(this.level, level, 1 - Math.exp(-dt * (level > this.level ? 18 : 6)));
      this.t += dt;
      this.boot = Math.min(1, this.boot + dt / 1.8);
      const speeds = [0.1, -0.2, 0.32, -0.55, 0.08];
      speeds.forEach((s, i) => { this.rot[i] += s * look.spin * dt * (1 + this.level * 1.5); });
      for (const p of this.particles) p.a += p.s * dt * 0.25 * look.spin;
      this.draw();
    }

    draw() {
      const { ctx, dpr } = this;
      const W = this.canvas.width;
      const H = this.canvas.height;
      const R = Math.min(W, H) * 0.44;
      const e = this.look.energy;
      const L = this.level;
      const boot = ease(this.boot);
      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.translate(W / 2, H / 2);
      ctx.globalCompositeOperation = "lighter";

      // halo
      const halo = ctx.createRadialGradient(0, 0, R * 0.05, 0, 0, R * 1.2);
      halo.addColorStop(0, this.color(0.3 * e + 0.08 + L * 0.25, 18));
      halo.addColorStop(0.35, this.color(0.1 * e + L * 0.08));
      halo.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(0, 0, R * 1.2, 0, TAU);
      ctx.fill();

      // particules en orbite
      for (const p of this.particles) {
        const rr = R * p.r * (0.6 + boot * 0.4);
        const x = Math.cos(p.a) * rr;
        const y = Math.sin(p.a) * rr * 0.92;
        ctx.fillStyle = this.color((0.15 + p.z * 0.5) * (0.4 + e * 0.6), 20);
        ctx.beginPath();
        ctx.arc(x, y, (0.6 + p.z * 1.4) * dpr, 0, TAU);
        ctx.fill();
      }

      // couronne de graduations réactive au son
      const ticks = 160;
      for (let i = 0; i < ticks; i++) {
        if (i / ticks > boot) break;
        const a = (i / ticks) * TAU + this.rot[4];
        const major = i % 10 === 0;
        const n = noise(i * 0.35 + this.t * 1.4);
        const len = R * (major ? 0.06 : 0.028) + R * 0.1 * L * n * e;
        const r0 = R * 1.02;
        ctx.strokeStyle = this.color((major ? 0.55 : 0.2) + 0.7 * L * n, major ? 12 : 0);
        ctx.lineWidth = (major ? 2 : 1) * dpr;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
        ctx.lineTo(Math.cos(a) * (r0 - len), Math.sin(a) * (r0 - len));
        ctx.stroke();
      }

      // anneaux segmentés
      const rings = [
        { r: 0.9, n: 3, w: 3.5, gap: 0.3, rot: 0 },
        { r: 0.82, n: 12, w: 1.2, gap: 0.55, rot: 1 },
        { r: 0.74, n: 36, w: 6, gap: 0.62, rot: 2 },
        { r: 0.47, n: 5, w: 2, gap: 0.42, rot: 3 },
      ];
      ctx.shadowColor = this.color(0.9, 10);
      rings.forEach((ring, index) => {
        const seg = TAU / ring.n;
        ctx.lineWidth = ring.w * dpr * (1 + L * 0.5);
        ctx.strokeStyle = this.color(0.35 + 0.45 * e + (index === 2 ? L * 0.3 : 0), index === 0 ? 14 : 0);
        ctx.shadowBlur = 16 * dpr * e;
        for (let j = 0; j < ring.n; j++) {
          const start = this.rot[ring.rot] + j * seg;
          ctx.beginPath();
          ctx.arc(0, 0, R * ring.r * (0.85 + 0.15 * boot), start, start + seg * (1 - ring.gap) * boot);
          ctx.stroke();
        }
      });

      // balayage de radar
      if (e > 0.5) {
        const sweep = ctx.createConicGradient ? ctx.createConicGradient(this.rot[2] * 2.2, 0, 0) : null;
        if (sweep) {
          sweep.addColorStop(0, this.color(0.22 * e, 10));
          sweep.addColorStop(0.08, this.color(0));
          sweep.addColorStop(1, this.color(0));
          ctx.fillStyle = sweep;
          ctx.beginPath();
          ctx.arc(0, 0, R * 0.9, 0, TAU);
          ctx.fill();
        }
      }

      // onde vocale circulaire
      ctx.shadowBlur = 26 * dpr;
      for (let layer = 0; layer < 2; layer++) {
        const base = R * (0.6 - layer * 0.035);
        ctx.beginPath();
        const N = 180;
        for (let i = 0; i <= N; i++) {
          const a = (i / N) * TAU;
          const wobble = noise(a * 3 + this.t * (2.2 + layer) + layer * 5);
          const amp = R * (0.01 + 0.13 * L * wobble * e + 0.012 * Math.sin(a * 8 + this.t * 3) * e);
          const rr = base + amp * (layer ? -0.7 : 1);
          const x = Math.cos(a) * rr;
          const y = Math.sin(a) * rr;
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.lineWidth = (layer ? 1 : 2.2) * dpr;
        ctx.strokeStyle = this.color(layer ? 0.35 : 0.9, 16);
        ctx.stroke();
      }

      // bobines du réacteur
      ctx.shadowBlur = 10 * dpr * e;
      const coils = 10;
      for (let i = 0; i < coils; i++) {
        const a = (i / coils) * TAU - this.rot[3] * 0.4;
        ctx.save();
        ctx.rotate(a);
        ctx.fillStyle = this.color(0.22 + 0.45 * e + L * 0.2, 6);
        const w = R * 0.085;
        const h = R * 0.04;
        ctx.beginPath();
        ctx.roundRect ? ctx.roundRect(R * 0.3, -h / 2, w, h, h * 0.3) : ctx.rect(R * 0.3, -h / 2, w, h);
        ctx.fill();
        ctx.restore();
      }

      // cœur
      const pulse = 0.2 + 0.018 * Math.sin(this.t * 2.3) + 0.075 * L * e;
      const coreR = R * pulse * (0.4 + 0.6 * boot);
      const core = ctx.createRadialGradient(0, 0, 0, 0, 0, coreR * 1.7);
      core.addColorStop(0, `hsla(${this.look.hue}, 100%, 97%, 0.98)`);
      core.addColorStop(0.28, this.color(0.9, 26));
      core.addColorStop(0.62, this.color(0.32, 6));
      core.addColorStop(1, "rgba(0,0,0,0)");
      ctx.shadowBlur = 40 * dpr * e;
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(0, 0, coreR * 1.7, 0, TAU);
      ctx.fill();

      // triangle central
      ctx.shadowBlur = 12 * dpr;
      ctx.strokeStyle = `hsla(${this.look.hue}, 100%, 94%, ${0.55 + 0.35 * e})`;
      ctx.lineWidth = 1.6 * dpr;
      ctx.beginPath();
      for (let i = 0; i <= 3; i++) {
        const a = -Math.PI / 2 + (i / 3) * TAU + this.rot[1] * 0.3;
        const x = Math.cos(a) * coreR * 0.62;
        const y = Math.sin(a) * coreR * 0.62;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.restore();

      // repères textuels
      ctx.save();
      ctx.translate(W / 2, H / 2);
      ctx.fillStyle = this.color(0.55, 10);
      ctx.font = `${10 * dpr}px "JetBrains Mono", monospace`;
      ctx.textAlign = "center";
      ["000", "090", "180", "270"].forEach((label, i) => {
        const a = (i / 4) * TAU - Math.PI / 2;
        ctx.globalAlpha = boot;
        ctx.fillText(label, Math.cos(a) * R * 1.12, Math.sin(a) * R * 1.12 + 3 * dpr);
      });
      ctx.restore();
    }
  }

  // ---------------------------------------------------------------- fond animé

  class Backdrop {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.t = 0;
      this.dots = Array.from({ length: 110 }, () => ({ x: Math.random(), y: Math.random(), z: Math.random(), v: 0.004 + Math.random() * 0.012 }));
      this.streaks = [];
      addEventListener("resize", () => this.resize());
      this.resize();
    }

    resize() {
      this.dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      this.canvas.width = innerWidth * this.dpr;
      this.canvas.height = innerHeight * this.dpr;
    }

    step(dt, look) {
      const { ctx, dpr } = this;
      const W = this.canvas.width;
      const H = this.canvas.height;
      this.t += dt;
      ctx.clearRect(0, 0, W, H);
      const hue = look.hue;

      // grille en perspective au sol
      ctx.save();
      ctx.strokeStyle = `hsla(${hue}, 80%, 60%, 0.06)`;
      ctx.lineWidth = dpr;
      const horizon = H * 0.58;
      const offset = (this.t * 24 * dpr) % (40 * dpr);
      for (let i = 0; i < 18; i++) {
        const y = horizon + Math.pow(i / 18, 2.2) * (H - horizon) + offset * (i / 18);
        ctx.globalAlpha = i / 18;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      for (let i = -14; i <= 14; i++) {
        ctx.beginPath();
        ctx.moveTo(W / 2 + i * 24 * dpr, horizon);
        ctx.lineTo(W / 2 + i * 140 * dpr, H);
        ctx.stroke();
      }
      ctx.restore();

      // poussière lumineuse
      for (const d of this.dots) {
        d.y -= d.v * dt * (0.4 + look.energy);
        if (d.y < -0.02) { d.y = 1.02; d.x = Math.random(); }
        const alpha = (0.12 + d.z * 0.35) * (0.5 + 0.5 * Math.sin(this.t * 2 + d.x * 20));
        ctx.fillStyle = `hsla(${hue}, 90%, 72%, ${alpha})`;
        ctx.beginPath();
        ctx.arc(d.x * W, d.y * H, (0.5 + d.z * 1.3) * dpr, 0, TAU);
        ctx.fill();
      }

      // traits de données
      if (Math.random() < dt * 0.8) this.streaks.push({ y: Math.random() * H, x: -0.2 * W, w: (0.1 + Math.random() * 0.25) * W, v: (0.6 + Math.random()) * W });
      this.streaks = this.streaks.filter((s) => s.x < W * 1.2);
      for (const s of this.streaks) {
        s.x += s.v * dt;
        const g = ctx.createLinearGradient(s.x, 0, s.x + s.w, 0);
        g.addColorStop(0, "rgba(0,0,0,0)");
        g.addColorStop(1, `hsla(${hue}, 100%, 70%, 0.14)`);
        ctx.fillStyle = g;
        ctx.fillRect(s.x, s.y, s.w, dpr);
      }
    }
  }

  // ---------------------------------------------------------------- interface

  const reactor = new Reactor($("#reactor"));
  const backdrop = new Backdrop($("#backdrop"));
  let typingTimer = null;

  function setState(name) {
    if (!STATES[name]) return;
    store.state = name;
    document.body.dataset.state = name;
    const s = STATES[name];
    $("#state-label").textContent = s.label;
    $("#reactor-label").textContent = s.title;
    $("#reactor-sub").textContent = s.sub;
  }

  function setEngine(active, model) {
    store.engine = { active, model };
    const sw = $("#engine-switch");
    sw.dataset.active = active;
    sw.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.engine === active)));
    $("#engine-name").textContent = active === "claude" ? "CLAUDE" : "LOCAL";
    $("#engine-model").textContent = model || "—";
  }

  function typeReply(text) {
    const el = $("#caption-jarvis");
    clearInterval(typingTimer);
    el.textContent = "";
    el.classList.add("typing");
    let i = 0;
    typingTimer = setInterval(() => {
      i += 2;
      el.textContent = text.slice(0, i);
      if (i >= text.length) { clearInterval(typingTimer); el.classList.remove("typing"); }
    }, 28);
  }

  function logEntry(kind, html) {
    const list = $("#log");
    const li = document.createElement("li");
    li.className = kind;
    li.innerHTML = `<time>${now()}</time>${html}`;
    list.appendChild(li);
    while (list.children.length > 90) list.firstChild.remove();
    list.scrollTop = list.scrollHeight;
    return li;
  }

  function renderLatency(marks) {
    const rows = MARKS.filter(([key]) => marks[key] !== undefined);
    const max = Math.max(1500, ...rows.map(([key]) => marks[key]));
    $("#latency").innerHTML = rows.map(([key, label]) =>
      `<li><span>${label}</span><span class="bar"><i style="width:0"></i></span><em>${marks[key]} ms</em></li>`).join("");
    requestAnimationFrame(() => {
      $("#latency").querySelectorAll("i").forEach((bar, i) => { bar.style.width = `${(marks[rows[i][0]] / max) * 100}%`; });
    });
    const total = marks.first_audio;
    $("#latency-total").textContent = total ? `${total} ms` : "";
  }

  function setGauge(id, value, suffix = "%") {
    const el = $(id);
    const v = value == null ? null : clamp(value, 0, 100);
    el.querySelector("strong").textContent = v == null ? "--" : `${Math.round(v)}${suffix === "%" ? "" : suffix}`;
    el.querySelector(".g-value").style.strokeDashoffset = String(263.9 * (1 - (v ?? 0) / 100));
    el.classList.toggle("warn", id !== "#gauge-bat" ? v >= 70 : v != null && v <= 30);
    el.classList.toggle("crit", id !== "#gauge-bat" ? v >= 90 : v != null && v <= 12);
  }

  function drawSpark() {
    const canvas = $("#cpu-spark");
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.clientWidth * dpr;
    const h = canvas.clientHeight * dpr;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    const data = store.cpuHistory;
    if (data.length < 2) return;
    const accent = getComputedStyle(document.body).getPropertyValue("--accent").trim() || "#5fd8ff";
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = (i / 59) * w;
      const y = h - (v / 100) * (h - 4 * dpr) - 2 * dpr;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = accent;
    ctx.lineWidth = 1.5 * dpr;
    ctx.shadowColor = accent;
    ctx.shadowBlur = 8 * dpr;
    ctx.stroke();
    ctx.lineTo(((data.length - 1) / 59) * w, h);
    ctx.lineTo(0, h);
    const fill = ctx.createLinearGradient(0, 0, 0, h);
    fill.addColorStop(0, `${accent}33`);
    fill.addColorStop(1, `${accent}00`);
    ctx.fillStyle = fill;
    ctx.shadowBlur = 0;
    ctx.fill();
  }

  function renderStatus(status) {
    if (!status) return;
    setGauge("#gauge-cpu", status.cpu);
    setGauge("#gauge-ram", status.ram);
    setGauge("#gauge-bat", status.battery);
    $("#sys-meta").textContent = status.battery == null ? "SECTEUR" : status.plugged ? "EN CHARGE" : "BATTERIE";
    store.cpuHistory.push(status.cpu ?? 0);
    if (store.cpuHistory.length > 60) store.cpuHistory.shift();
    drawSpark();
    store.timers = status.timers || [];
  }

  function renderTimers() {
    const list = $("#timers");
    if (!store.timers.length) { list.innerHTML = '<li class="empty">Aucun minuteur</li>'; return; }
    list.innerHTML = store.timers.map((t) => {
      const left = Math.max(0, Math.round(t.ends_at - Date.now() / 1000));
      const mm = String(Math.floor(left / 60)).padStart(2, "0");
      const ss = String(left % 60).padStart(2, "0");
      return `<li><span>${esc(t.label || "Minuteur")}</span><b>${mm}:${ss}</b></li>`;
    }).join("");
  }

  function tickClock() {
    const d = new Date();
    $("#clock-time").textContent = d.toLocaleTimeString("fr-FR");
    $("#clock-date").textContent = d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
    renderTimers();
  }

  // ---------------------------------------------------------------- confirmation

  function openConfirm(event) {
    $("#confirm-question").textContent = event.question;
    $("#confirm-always").hidden = !event.always;
    $("#confirm-kicker").textContent = event.always ? "AUTORISATION REQUISE" : "ACTION CRITIQUE";
    $("#confirm").hidden = false;
    const ring = $("#confirm-ring");
    const total = (store.confirmTimeout + 2.5) * 1000;
    const start = performance.now();
    cancelAnimationFrame(store.confirmTimer);
    const loop = (t) => {
      const p = clamp((t - start) / total, 0, 1);
      ring.style.strokeDashoffset = String(339.3 * p);
      if (p < 1 && !$("#confirm").hidden) store.confirmTimer = requestAnimationFrame(loop);
    };
    store.confirmTimer = requestAnimationFrame(loop);
    logEntry("tool", `<div class="tool-head"><span class="tool-name">DEMANDE</span><span class="chip ${event.always ? "N2" : "N3"}">${event.always ? "N2" : "N3"}</span></div><div>${esc(event.question)}</div>`);
  }

  function closeConfirm() {
    $("#confirm").hidden = true;
    cancelAnimationFrame(store.confirmTimer);
  }

  async function decide(decision) {
    closeConfirm();
    try { await api("api/action", { action: "decide", value: decision }); } catch (err) { toast(err.message, "error"); }
  }

  // ---------------------------------------------------------------- événements

  STATES.loading = { label: "INITIALISATION", title: "INITIALISATION", sub: "CHARGEMENT DES MODÈLES", hue: 200, sat: 70, light: 60, energy: 0.55, spin: 1.8 };

  function handle(event) {
    switch (event.type) {
      case "state": setState(event.state); break;
      case "screen":
        store.screenAt = event.at;
        $("#activity-app").textContent = (event.app || "ÉCRAN").toUpperCase();
        $("#activity-text").textContent = event.text;
        $("#activity").classList.remove("fresh");
        void $("#activity").offsetWidth;
        $("#activity").classList.add("fresh");
        if (!store.screenTimer) {
          store.screenTimer = setInterval(() => {
            const age = Math.max(0, Math.round(Date.now() / 1000 - store.screenAt));
            $("#activity-age").textContent = age < 60 ? `il y a ${age} s` : `il y a ${Math.round(age / 60)} min`;
          }, 1000);
        }
        break;
      case "loading":
        $("#reactor-sub").textContent = `PRÊT · ${String(event.label).toUpperCase()}`;
        logEntry("announce", `<span class="k">SYSTÈME</span>${esc(event.label)} prêt en ${event.ms} ms`);
        break;
      case "levels": store.mic = event.mic; store.out = event.out; break;
      case "engine": setEngine(event.active, event.model); break;
      case "near_miss":
        logEntry("announce", `<span class="k">ÉCOUTE</span>J'ai cru entendre « Hey Jarvis » (score ${event.score}, seuil ${event.threshold}) — baisse la sensibilité dans les réglages si ça se répète`);
        break;
      case "browsers": {
        const names = event.names || [];
        logEntry("announce", `<span class="k">NAVIGATEUR</span>${names.length ? `${esc(names.join(", "))} relié à Jarvis` : "Aucun navigateur relié"}`);
        break;
      }
      case "editors": {
        const names = event.names || [];
        logEntry("announce", `<span class="k">ÉDITEUR</span>${names.length ? `${esc(names.join(", "))} relié à Jarvis` : "Aucun éditeur relié"}`);
        break;
      }
      case "review_started":
        store.reviewLi = logEntry("review", `<span class="k">REVIEW</span>${esc(event.label)} lancée avec ${event.engine === "claude" ? "Claude" : "le modèle local"} <span class="tool-args review-progress"></span>`);
        break;
      case "review_progress": {
        const progress = store.reviewLi && store.reviewLi.querySelector(".review-progress");
        if (progress) progress.textContent = `${event.done}/${event.total}`;
        break;
      }
      case "review_cancelled":
        logEntry("announce", `<span class="k">REVIEW</span>${esc(event.label)} arrêtée`);
        break;
      case "review":
        logEntry("review",
          `<div class="tool-head"><span class="tool-name">REVIEW</span><span class="chip N1">${event.engine === "claude" ? "CLAUDE" : "LOCAL"}</span></div>` +
          `<div>${esc(event.summary)}</div>` + (event.note ? `<div class="tool-args">${esc(event.note)}</div>` : "") +
          `<details><summary>Rapport complet</summary><pre class="report">${esc(event.report)}</pre>` +
          `<div class="tool-args">${esc(event.path)}</div></details>`);
        break;
      case "user":
        $("#caption-user").textContent = event.text;
        logEntry("user", `<span class="k">TOI</span>${esc(event.text)}`);
        break;
      case "reply":
        if (!event.text) break;
        typeReply(event.text);
        logEntry("jarvis", `<span class="k">JARVIS</span>${esc(event.text)}${event.interrupted ? " <em>(interrompu)</em>" : ""}`);
        break;
      case "announce":
        typeReply(event.text);
        logEntry("announce", `<span class="k">ANNONCE</span>${esc(event.text)}`);
        break;
      case "tool": {
        const args = Object.entries(event.arguments || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");
        const li = logEntry(`tool${event.allowed ? "" : " denied"}`,
          `<div class="tool-head"><span class="tool-name">${esc(TOOL_LABELS[event.name] || event.name)}</span>` +
          `<span class="chip ${esc(event.level)}">${esc(event.level)}</span></div>` +
          (args ? `<div class="tool-args">${esc(args)}</div>` : "") +
          `<div>${esc(event.text)} <span class="tool-args">${event.ms} ms</span></div>`);
        li.title = event.name;
        break;
      }
      case "confirm": openConfirm(event); break;
      case "confirm_done": closeConfirm(); break;
      case "metrics": renderLatency(event.marks || {}); break;
      case "status": renderStatus(event); break;
      case "error": logEntry("error", esc(event.text)); toast(event.text, "error"); break;
      default: break;
    }
  }

  function connect() {
    if (DEMO) { Demo.start(handle); return; }
    const source = new EventSource("api/events");
    source.onmessage = (message) => { try { handle(JSON.parse(message.data)); } catch (err) { console.error(err); } };
    source.onerror = () => setState("offline");
  }

  // ---------------------------------------------------------------- réglages

  function fieldValue(key) {
    return store.dirty.has(key) ? store.dirty.get(key) : getPath(store.data.config, key);
  }

  function markDirty(key, value) {
    const original = getPath(store.data.config, key);
    if (JSON.stringify(original) === JSON.stringify(value)) store.dirty.delete(key); else store.dirty.set(key, value);
    const count = store.dirty.size;
    $("#save-status").textContent = count ? `${count} modification${count > 1 ? "s" : ""} en attente` : "Aucune modification";
    $("#save-btn").disabled = !count;
    $("#reset-btn").disabled = !count;
    document.querySelectorAll(`[data-key="${CSS.escape(key)}"]`).forEach((el) => el.closest(".field")?.classList.toggle("dirty", store.dirty.has(key)));
  }

  function badge(field) {
    return field.live ? '<span class="badge live">EN DIRECT</span>' : '<span class="badge restart">REDÉMARRAGE</span>';
  }

  function renderField(field) {
    const value = fieldValue(field.key);
    const id = `f-${field.key.replace(/\W/g, "-")}`;
    const help = field.help ? `<p class="help">${esc(field.help)}</p>` : "";
    const head = (control = "") => `<div class="field-head"><label for="${id}">${esc(field.label)}</label>${control}${badge(field)}</div>`;
    const wrap = document.createElement("div");
    wrap.className = `field${store.dirty.has(field.key) ? " dirty" : ""}`;
    if (field.type === "toggle") {
      wrap.innerHTML = `<div class="field-head"><label for="${id}">${esc(field.label)}</label><span style="flex:1"></span>${badge(field)}<span class="toggle"><input id="${id}" type="checkbox" data-key="${esc(field.key)}" ${value ? "checked" : ""}><span></span></span></div>${help}`;
      wrap.querySelector("input").onchange = (e) => markDirty(field.key, e.target.checked);
    } else if (field.type === "select") {
      const options = field.options.map(([v, label]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(label)}</option>`).join("");
      wrap.innerHTML = `${head()}<select id="${id}" data-key="${esc(field.key)}">${options}</select>${help}`;
      wrap.querySelector("select").onchange = (e) => markDirty(field.key, e.target.value);
    } else if (field.type === "slider") {
      const fmt = (v) => `${Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 2 })}${field.unit ? ` ${field.unit}` : ""}`;
      wrap.innerHTML = `${head()}<div class="slider-row"><input id="${id}" type="range" min="${field.min}" max="${field.max}" step="${field.step}" value="${value}" data-key="${esc(field.key)}"><span class="value">${fmt(value)}</span></div>${help}`;
      const input = wrap.querySelector("input");
      const out = wrap.querySelector(".value");
      const paint = () => { input.style.setProperty("--fill", `${((input.value - field.min) / (field.max - field.min)) * 100}%`); out.textContent = fmt(input.value); };
      input.oninput = () => { paint(); markDirty(field.key, Number(input.value)); };
      paint();
    } else {
      wrap.innerHTML = `${head()}<input id="${id}" type="${field.type === "number" ? "number" : "text"}" value="${esc(value ?? "")}" data-key="${esc(field.key)}" spellcheck="false">${help}`;
      wrap.querySelector("input").oninput = (e) => markDirty(field.key, field.type === "number" ? Number(e.target.value) : e.target.value);
    }
    return wrap;
  }

  function renderPermissions(body) {
    const disabled = new Set(fieldValue("tools.disabled") || []);
    const always = new Set(fieldValue("tools.always_allow") || []);
    const levels = { N1: "Sans confirmation", N2: "Confirmation, mémorisable", N3: "Confirmation à chaque fois" };
    body.insertAdjacentHTML("beforeend", '<p class="section-intro">Ce que Jarvis a le droit de faire sur ton ordinateur. Les actions N3 demandent toujours ton accord.</p>');
    body.appendChild(renderField({ key: "tools.enabled", label: "Actions sur l'ordinateur", type: "toggle", live: true, help: "Coupe tout d'un coup : Jarvis ne fera plus que parler." }));
    for (const tool of store.data.tools) {
      const card = document.createElement("div");
      card.className = "field perm";
      card.innerHTML = `
        <div class="perm-title">${esc(TOOL_LABELS[tool.name] || tool.name)} <span class="chip ${tool.level}">${tool.level}</span></div>
        <span class="badge live">EN DIRECT</span>
        <p class="perm-desc">${esc(tool.description)} <br><span class="tool-args">${levels[tool.level]}</span></p>
        <div class="perm-controls">
          <label><span class="toggle"><input type="checkbox" data-role="enabled" ${disabled.has(tool.name) ? "" : "checked"}><span></span></span>Autorisée</label>
          ${tool.level === "N2" ? `<label><span class="toggle"><input type="checkbox" data-role="always" ${always.has(tool.name) ? "checked" : ""}><span></span></span>Sans me demander</label>` : ""}
        </div>`;
      card.querySelector('[data-role="enabled"]').onchange = (e) => {
        const set = new Set(fieldValue("tools.disabled") || []);
        e.target.checked ? set.delete(tool.name) : set.add(tool.name);
        markDirty("tools.disabled", [...set].sort());
        card.classList.toggle("dirty", true);
      };
      const alwaysBox = card.querySelector('[data-role="always"]');
      if (alwaysBox) alwaysBox.onchange = (e) => {
        const set = new Set(fieldValue("tools.always_allow") || []);
        e.target.checked ? set.add(tool.name) : set.delete(tool.name);
        markDirty("tools.always_allow", [...set].sort());
        card.classList.toggle("dirty", true);
      };
      body.appendChild(card);
    }
  }

  function renderSettings() {
    const { sections } = store.data;
    if (!store.tab) store.tab = sections[0].id;
    $("#tabs").innerHTML = [...sections, { id: "permissions", title: "Permissions" }, { id: "about", title: "Fichier" }]
      .map((s) => `<button type="button" role="tab" data-tab="${s.id}" aria-selected="${s.id === store.tab}">${esc(s.title.toUpperCase())}</button>`).join("");
    const body = $("#drawer-body");
    body.innerHTML = "";
    body.scrollTop = 0;
    if (store.tab === "permissions") { renderPermissions(body); return; }
    if (store.tab === "about") {
      body.innerHTML = `<p class="section-intro">Les réglages sont enregistrés dans ce fichier. Tu peux aussi le modifier à la main ; les commentaires sont dans <b>config.example.yaml</b>.</p><div class="field"><div class="path">${esc(store.data.config_path)}</div></div><div class="field"><label>Version</label><div class="path">Jarvis ${esc(store.data.version)}</div></div>`;
      return;
    }
    const section = sections.find((s) => s.id === store.tab);
    if (section.intro) body.insertAdjacentHTML("beforeend", `<p class="section-intro">${esc(section.intro)}</p>`);
    section.fields.forEach((field) => body.appendChild(renderField(field)));
  }

  async function openSettings() {
    try {
      store.data = await api("api/state");
    } catch (err) {
      toast(`Réglages indisponibles : ${err.message}`, "error");
      return;
    }
    store.dirty.clear();
    markDirty("__none__", undefined);
    renderSettings();
    $("#drawer").classList.add("open");
    $("#drawer").setAttribute("aria-hidden", "false");
    $("#drawer-backdrop").hidden = false;
    document.body.classList.add("drawer-open");
  }

  function closeSettings() {
    document.body.classList.remove("drawer-open");
    $("#drawer").classList.remove("open");
    $("#drawer").setAttribute("aria-hidden", "true");
    $("#drawer-backdrop").hidden = true;
  }

  function nest(entries) {
    const out = {};
    for (const [key, value] of entries) {
      const parts = key.split(".");
      let node = out;
      parts.slice(0, -1).forEach((p) => { node = node[p] = node[p] || {}; });
      node[parts.at(-1)] = value;
    }
    return out;
  }

  async function saveSettings() {
    const button = $("#save-btn");
    button.disabled = true;
    $("#save-status").textContent = "Enregistrement…";
    try {
      const result = await api("api/config", { updates: nest(store.dirty.entries()) });
      store.data = result.state;
      store.dirty.clear();
      markDirty("__none__", undefined);
      renderSettings();
      if (result.restart.length) {
        toast(`Enregistré. ${result.restart.length} réglage${result.restart.length > 1 ? "s" : ""} prendront effet au redémarrage.`, "warn",
          { label: "Redémarrer Jarvis", run: () => api("api/action", { action: "restart" }).then(() => toast("Redémarrage…")) });
      } else {
        toast("Enregistré et appliqué en direct.", "ok");
      }
    } catch (err) {
      $("#save-status").textContent = err.message;
      button.disabled = false;
      toast(err.message, "error");
    }
  }

  // ---------------------------------------------------------------- démo (sans serveur)

  const Demo = {
    config: {
      user_name: "Sacha", llm: { backend: "ollama", model: "qwen3.5:4b-mlx", temperature: 0.6, history_turns: 6 },
      claude: { model: "haiku", fallback_to_local: true }, vad: { end_silence_ms: 550, threshold: 0.5 },
      wakeword: { threshold: 0.5 }, audio: { follow_up_s: 4 }, tts: { voice: "fr_FR-siwis-medium", length_scale: 1 },
      stt: { model: "mlx-community/whisper-large-v3-turbo-q4" },
      tools: { enabled: true, always_allow: [], disabled: [], confirm_timeout_s: 6 },
    },
    api(path, body) {
      if (path === "api/state") return Promise.resolve(Demo.state());
      if (path === "api/config") {
        const walk = (target, src) => Object.entries(src).forEach(([k, v]) => { if (v && typeof v === "object" && !Array.isArray(v)) walk(target[k], v); else target[k] = v; });
        walk(Demo.config, body.updates);
        return Promise.resolve({ state: Demo.state(), applied: [], restart: [] });
      }
      if (path === "api/action" && body.action === "switch") { Demo.emit({ type: "engine", active: body.value, model: body.value === "claude" ? "haiku" : "qwen3.5:4b-mlx" }); return Promise.resolve({ message: "C'est fait." }); }
      if (path === "api/action" && body.action === "decide") { Demo.emit({ type: "confirm_done", decision: body.value }); return Promise.resolve({}); }
      return Promise.resolve({});
    },
    state() {
      return {
        version: "démo", config_path: "~/Library/Application Support/jarvis/config.yaml", config: Demo.config,
        tools: Object.keys(TOOL_LABELS).map((name) => ({ name, description: TOOL_LABELS[name], level: ["close_app", "lock_screen"].includes(name) ? "N2" : name === "power" ? "N3" : "N1" })),
        sections: [
          { id: "general", title: "Général", fields: [
            { key: "user_name", label: "Ton prénom", type: "text", live: true, help: "Jarvis s'adresse à toi par ce prénom." },
            { key: "llm.backend", label: "Moteur au démarrage", type: "select", live: true, options: [["ollama", "Local (Ollama)"], ["claude", "Claude (abonnement)"]] },
            { key: "audio.follow_up_s", label: "Relance sans « Hey Jarvis »", type: "slider", min: 0, max: 10, step: 0.5, unit: "s", live: true },
          ] },
          { id: "voice", title: "Voix & écoute", fields: [
            { key: "vad.end_silence_ms", label: "Silence de fin de phrase", type: "slider", min: 250, max: 1500, step: 25, unit: "ms", live: true },
            { key: "wakeword.threshold", label: "Sensibilité du mot d'activation", type: "slider", min: 0.2, max: 0.9, step: 0.05, live: true },
            { key: "tts.length_scale", label: "Débit de la voix", type: "slider", min: 0.7, max: 1.3, step: 0.05, live: true },
          ] },
        ],
      };
    },
    emit(event) { Demo.handler(event); },
    start(handler) {
      Demo.handler = handler;
      handler({ type: "engine", active: "local", model: "qwen3.5:4b-mlx" });
      handler({ type: "state", state: "sleeping" });
      handler({ type: "screen", app: "Visual Studio Code", text: "Sacha modifie le fichier pipeline.py d'un projet Python.", at: Date.now() / 1000 });
      const script = [
        [1500, { type: "state", state: "listening" }], [2600, { type: "user", text: "Ouvre Spotify et mets le son à trente" }],
        [300, { type: "state", state: "thinking" }],
        [700, { type: "tool", name: "open_app", arguments: { name: "Spotify" }, level: "N1", text: "J'ouvre Spotify.", allowed: true, ms: 84 }],
        [200, { type: "tool", name: "set_volume", arguments: { level: 30 }, level: "N1", text: "Volume à 30 pour cent.", allowed: true, ms: 61 }],
        [100, { type: "state", state: "speaking" }], [50, { type: "reply", text: "J'ouvre Spotify. Volume à 30 pour cent." }],
        [100, { type: "metrics", marks: { stt: 362, fastpath: 371, first_audio: 448 } }],
        [3000, { type: "state", state: "listening" }], [2400, { type: "user", text: "Ferme Discord" }],
        [300, { type: "state", state: "confirm" }], [100, { type: "confirm", question: "Je ferme Discord ?", always: true }],
        [4200, { type: "confirm_done", decision: "yes" }],
        [200, { type: "tool", name: "close_app", arguments: { name: "Discord" }, level: "N2", text: "Je ferme Discord.", allowed: true, ms: 140 }],
        [100, { type: "state", state: "speaking" }], [50, { type: "reply", text: "Je ferme Discord." }],
        [2500, { type: "state", state: "sleeping" }], [3500, { type: "announce", text: "Minuteur terminé : les pâtes." }],
      ];
      let i = 0;
      const next = () => { const [delay, event] = script[i % script.length]; i += 1; setTimeout(() => { handler(event); next(); }, delay); };
      next();
      setInterval(() => {
        const s = store.state;
        const mic = s === "listening" ? 0.02 + Math.random() * 0.12 : 0.004;
        const out = s === "speaking" ? 0.05 + Math.random() * 0.2 : 0;
        handler({ type: "levels", mic, out });
      }, 90);
      const cpu = { v: 18 };
      setInterval(() => {
        cpu.v = clamp(cpu.v + (Math.random() - 0.45) * 12, 4, 96);
        handler({ type: "status", cpu: cpu.v, ram: 63, battery: 91, plugged: false, timers: [{ id: 1, label: "Pâtes", ends_at: Date.now() / 1000 + 312 }] });
      }, 1500);
    },
  };

  // ---------------------------------------------------------------- démarrage

  function bootSequence() {
    const lines = ["Noyau vocal", "Mot d'activation", "Transcription", "Moteur de réponse", "Outils système", "Interface"];
    const list = $("#boot-lines");
    lines.forEach((line, i) => setTimeout(() => {
      list.insertAdjacentHTML("beforeend", `<li style="animation-delay:0s">› ${line}<b>OK</b></li>`);
    }, 180 + i * 210));
    setTimeout(() => { $("#boot").classList.add("done"); document.body.classList.add("ready"); }, 1650);
  }

  function bind() {
    $("#btn-talk").onclick = () => api("api/action", { action: "wake" }).catch((e) => toast(e.message, "error"));
    $("#btn-stop").onclick = () => api("api/action", { action: "stop" }).catch((e) => toast(e.message, "error"));
    $("#btn-log").onclick = () => $("#panel-log").classList.toggle("show");
    $("#clear-log").onclick = () => { $("#log").innerHTML = ""; };
    $("#open-settings").onclick = openSettings;
    $("#close-settings").onclick = closeSettings;
    $("#drawer-backdrop").onclick = closeSettings;
    $("#save-btn").onclick = saveSettings;
    $("#reset-btn").onclick = () => { store.dirty.clear(); markDirty("__none__", undefined); renderSettings(); };
    $("#tabs").onclick = (e) => { const tab = e.target.closest("[data-tab]"); if (tab) { store.tab = tab.dataset.tab; renderSettings(); } };
    $("#engine-switch").onclick = async (e) => {
      const button = e.target.closest("[data-engine]");
      if (!button || button.dataset.engine === store.engine.active) return;
      try {
        const result = await api("api/action", { action: "switch", value: button.dataset.engine });
        toast(result.message || "Moteur changé.", result.ok === false ? "warn" : "ok");
      } catch (err) { toast(err.message, "error"); }
    };
    document.querySelectorAll("[data-decision]").forEach((b) => { b.onclick = () => decide(b.dataset.decision); });
    addEventListener("keydown", (e) => {
      const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
      if (!$("#confirm").hidden) {
        if (e.key === "Enter") { e.preventDefault(); decide("yes"); }
        if (e.key === "Escape") { e.preventDefault(); decide("no"); }
        return;
      }
      if (e.key === "Escape") closeSettings();
      if (typing) return;
      if (e.code === "Space") { e.preventDefault(); $("#btn-talk").click(); }
      if (e.key.toLowerCase() === "s") $("#btn-stop").click();
      if (e.key === ",") openSettings();
    });
  }

  let last = performance.now();
  function frame(t) {
    const dt = Math.min(0.05, (t - last) / 1000);
    last = t;
    const level = clamp(Math.max(store.mic * 7, store.out * 4.5), 0, 1);
    reactor.step(dt, STATES[store.state], level);
    backdrop.step(dt, reactor.look);
    requestAnimationFrame(frame);
  }

  bind();
  bootSequence();
  tickClock();
  setInterval(tickClock, 1000);
  connect();
  if (!DEMO) {
    const poll = () => api("api/status").then(renderStatus).catch(() => {});
    poll();
    setInterval(poll, 2000);
    api("api/state").then((data) => {
      $("#brand-version").textContent = `v${data.version}`;
      store.confirmTimeout = data.config.tools.confirm_timeout_s;
    }).catch(() => {});
  } else {
    $("#brand-version").textContent = "démo";
  }
  requestAnimationFrame(frame);
})();
