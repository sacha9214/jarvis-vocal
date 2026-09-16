// Actions exécutées DANS la page. Chaque fonction est autonome (aucune variable extérieure) :
// l'extension l'injecte telle quelle (par son code source : une expression `function`, pas une méthode
// raccourcie, sinon Firefox refuse l'injection en silence), et Jarvis la réutilise pour Safari via AppleScript.
globalThis.JarvisActions = {
  media: function (params) {
    const all = [...document.querySelectorAll("video, audio")];
    if (!all.length) return { found: false };
    const score = (m) => {
      const r = m.getBoundingClientRect();
      return (m.paused ? 0 : 1e12) + r.width * r.height + (m.duration || 0);
    };
    const media = all.sort((a, b) => score(b) - score(a))[0];
    // YouTube : passer par son lecteur garde l'interface synchronisée (curseur de volume, sourdine).
    const yt = location.hostname.endsWith("youtube.com") ? document.getElementById("movie_player") : null;
    const volumeNow = () => Math.round(yt && yt.getVolume ? yt.getVolume() : media.volume * 100);
    const setVolume = (value) => {
      const v = Math.max(0, Math.min(100, Math.round(value)));
      if (yt && yt.setVolume) {
        yt.setVolume(v);
        if (v > 0 && yt.unMute) yt.unMute();
      }
      media.volume = v / 100;
      if (v > 0) media.muted = false;
    };
    const step = Number(params.value) || 10;
    switch (params.action) {
      case "play": media.play(); break;
      case "pause": media.pause(); break;
      case "toggle": if (media.paused) media.play(); else media.pause(); break;
      case "volume": setVolume(Number(params.value)); break;
      case "volume_up": setVolume(volumeNow() + step); break;
      case "volume_down": setVolume(volumeNow() - step); break;
      case "mute": media.muted = true; if (yt && yt.mute) yt.mute(); break;
      case "unmute": media.muted = false; if (yt && yt.unMute) yt.unMute(); break;
      case "forward": media.currentTime = Math.min(media.duration || Infinity, media.currentTime + step); break;
      case "back": media.currentTime = Math.max(0, media.currentTime - step); break;
      case "seek": media.currentTime = Math.max(0, Number(params.value) || 0); break;
      case "speed": media.playbackRate = Math.max(0.25, Math.min(3, Number(params.value) || 1)); break;
      case "next": {
        const next = document.querySelector(".ytp-next-button");
        if (!next) return { found: true, error: "Pas de vidéo suivante sur ce site." };
        next.click();
        break;
      }
      case "status": break;
      default: return { found: true, error: `Action inconnue : ${params.action}` };
    }
    return {
      found: true, paused: media.paused, muted: media.muted || Boolean(yt && yt.isMuted && yt.isMuted()),
      volume: volumeNow(), time: Math.round(media.currentTime), duration: Math.round(media.duration || 0),
      speed: media.playbackRate,
    };
  },

  page: function (params) {
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 4 && r.height > 4 && s.visibility !== "hidden" && s.display !== "none";
    };
    const label = (el) => (el.getAttribute("aria-label") || el.getAttribute("title") || el.innerText || el.value || "")
      .replace(/\s+/g, " ").trim().slice(0, 140);
    const isVideo = (el) => /\/watch\?|youtu\.be\/|\/shorts\/|\/video\/|vimeo\.com\/\d|dailymotion\.com\/video/.test(el.href || "");
    const pool = [...document.querySelectorAll("a[href], button, [role=button], [role=link], [role=tab], input[type=submit]")]
      .filter((el) => visible(el) && label(el).length > 1);
    const items = [];
    const seen = new Set();
    for (const el of [...pool.filter(isVideo), ...pool.filter((el) => !isVideo(el))]) {
      const key = `${label(el)}|${el.href || ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      items.push(el);
      if (items.length >= (params.max_items || 60)) break;
    }
    window.__jarvisItems = items;
    const main = document.querySelector("main, article, [role=main]") || document.body;
    return {
      text: (main.innerText || "").replace(/\n{3,}/g, "\n\n").slice(0, params.max_chars || 6000),
      selection: String(getSelection() || "").slice(0, 2000),
      items: items.map((el, i) => ({
        index: i + 1, text: label(el), href: el.href || null,
        kind: isVideo(el) ? "vidéo" : el.tagName === "A" ? "lien" : "bouton",
      })),
    };
  },

  click: function (params) {
    const norm = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[^a-z0-9]+/g, " ").trim();
    const label = (el) => (el.getAttribute("aria-label") || el.getAttribute("title") || el.innerText || el.value || "")
      .replace(/\s+/g, " ").trim().slice(0, 140);
    let target = null;
    const items = window.__jarvisItems || [];
    if (params.index && items[params.index - 1] && items[params.index - 1].isConnected) target = items[params.index - 1];
    if (!target && params.text) {
      const query = norm(params.text);
      const words = query.split(" ").filter((w) => w.length > 2);
      let best = 0;
      for (const el of document.querySelectorAll("a[href], button, [role=button], [role=link], [role=tab], [role=menuitem], input[type=submit]")) {
        const text = norm(label(el));
        if (!text) continue;
        const score = text.includes(query) ? 100 - Math.min(99, text.length - query.length) / 4
          : (words.filter((w) => text.includes(w)).length / Math.max(1, words.length)) * 60;
        if (score > best) { best = score; target = el; }
      }
      if (best < 30) target = null;
    }
    if (!target) return { clicked: false, error: "Je ne trouve pas cet élément sur la page." };
    const text = label(target);
    // Achat, suppression, envoi… : Jarvis ne clique pas à ta place.
    if (params.forbid && new RegExp(params.forbid, "i").test(norm(text))) {
      return { clicked: false, text, error: "C'est une action sensible : fais-la toi-même." };
    }
    target.scrollIntoView({ block: "center" });
    target.click();
    return { clicked: true, text, href: target.href || null, kind: target.tagName === "A" ? "lien" : "bouton" };
  },

  scroll: function (params) {
    const height = innerHeight * 0.8;
    if (params.direction === "top") scrollTo({ top: 0, behavior: "smooth" });
    else if (params.direction === "bottom") scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    else scrollBy({ top: params.direction === "up" ? -height : height, behavior: "smooth" });
    return { scrolled: params.direction };
  },

  type: function (params) {
    const el = document.activeElement;
    const editable = el && (el.isContentEditable || "value" in el) && el !== document.body;
    if (!editable) return { typed: false, error: "Aucun champ de saisie n'est sélectionné sur la page." };
    if (el.isContentEditable) {
      document.execCommand("insertText", false, params.text);
    } else {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value")?.set;
      const value = `${el.value || ""}${params.text}`;
      if (setter) setter.call(el, value); else el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (params.submit) {
      if (el.form && el.form.requestSubmit) el.form.requestSubmit();
      else el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
    }
    return { typed: true };
  },
};
