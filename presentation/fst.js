/* AURA Subsystem 02 in the browser: a line-by-line port of FSTDialogueManager, parse_utterance,
 * strip_noise and NoisyChannel from 04_fst_noisy_simulator.py.
 * All vocabularies and regular expressions come from the Python engine (spec = MISSION_DATA.spec),
 * and build_data.py checks that this port reproduces the Python state traces exactly. */
(function (root) {
  "use strict";

  function create(spec) {
    const rx = (p) => new RegExp(p.source, p.flags);
    const R = Object.fromEntries(Object.entries(spec.regex).map(([k, v]) => [k, rx(v)]));
    const NOISE = spec.noise_vocab, NOISE_SET = new Set(NOISE.map((w) => w.toLowerCase()));
    const escRx = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const DEST_RX = spec.destinations.map((d) => [d, new RegExp("\\b" + escRx(d.toLowerCase()) + "\\b")]);
    const NOISE_RX = new RegExp("\\b(" + NOISE.join("|") + ")\\b[,.;!?]?", "gi");
    const OUT = spec.outputs, ORDER = spec.slot_order, AUTH = new Set(spec.clearances), NUM = spec.number_words;
    const fmt = (t, o) => t.replace(/\{(\w+)\}/g, (_, k) => o[k]);

    function stripNoise(text) {
      const removed = [];
      const cleaned = text.replace(NOISE_RX, (m) => { removed.push(m); return " "; }).replace(/\s+/g, " ").trim();
      return [cleaned, removed];
    }

    function parse(text, hardened) {
      const [cleaned, removed] = hardened ? stripNoise(text) : [text, []];
      const slots = {}, invalid = {}, low = cleaned.toLowerCase();
      for (const [d, r] of DEST_RX) if (r.test(low)) { slots.destination = d; break; }
      const m = cleaned.match(R.speed);
      let n = m ? parseInt(m[1], 10) : null;
      if (n === null && hardened) { const mw = cleaned.match(R.speed_word); n = mw ? NUM[mw[1].toLowerCase()] : null; }
      if (n !== null) { if (n >= 1 && n <= 9) slots.speed = "Warp " + n; else invalid.speed = "Warp " + n; }
      for (const mm of cleaned.matchAll(new RegExp(R.clearance.source, "g"))) {
        const code = mm[1].toUpperCase();
        if (code.startsWith("WARP")) continue;
        if (!hardened || AUTH.has(code)) { slots.clearance = code; break; }
        invalid.clearance = code;
      }
      return { raw: text, cleaned, stripped_noise: removed, greet: R.greeting.test(cleaned), intent: R.intent.test(cleaned),
               affirm: R.affirm.test(cleaned), negate: R.negate.test(cleaned),
               injection: hardened && R.injection.test(cleaned), sarcasm: hardened && R.sarcasm.test(cleaned), slots, invalid };
    }

    class FST {
      constructor(hardened = true, maxRetries = 3) {
        Object.assign(this, { hardened, maxRetries, state: "INIT", resume: "INIT", slot_memory: {}, fallback_count: 0,
                              outcome: null, turn: 0, trace: [], security_flags: [] });
      }
      missing() { return ORDER.filter((s) => !(s in this.slot_memory)); }
      process(u) {
        this.turn++;
        const p = parse(u, this.hardened), source = this.state;
        const [target, output, rule, flags] = this.step(p);
        this.state = target;
        if (target !== "FALLBACK") this.resume = target;
        const t = { turn: this.turn, utterance: u, source, target, output, rule, slot_memory: { ...this.slot_memory },
                    fallback_count: this.fallback_count,
                    flags: flags.concat(p.stripped_noise.length ? ["noise stripped: " + p.stripped_noise.join(", ")] : []), parsed: p };
        this.trace.push(t);
        return t;
      }
      step(p) {
        let s = this.state;
        if (s === "TERMINAL") return [s, fmt(OUT.closed, { outcome: this.outcome }), "absorbing terminal state", []];
        if (p.injection) { this.security_flags.push(p.raw);
          return this.fallback("security: prompt-injection pattern blocked", fmt(OUT.security, { hint: this.hint() }), ["SECURITY"]); }
        if (s === "FALLBACK") s = this.resume;
        if (s === "INIT") {
          if (p.greet) return ["GREETING", OUT.welcome, "greeting recognised", []];
          if (this.hardened && (p.intent || Object.keys(p.slots).length)) return this.after("contact by command" + filled(this.absorb(p)));
          return this.fallback("no greeting in INIT", fmt(OUT.fallback, { hint: "Please greet first." }));
        }
        if (s === "GREETING") {
          if (p.intent || Object.keys(p.slots).length) return this.after("intent recognised" + filled(this.absorb(p)));
          if (p.greet) return ["GREETING", OUT.welcome, "repeated greeting", []];
          return this.fallback("intent not recognised", fmt(OUT.fallback, { hint: OUT.welcome }));
        }
        if (s === "COLLECT_SLOTS") {
          const f = this.absorb(p);
          if (Object.keys(f).length) return this.after("slot extracted" + filled(f));
          const why = "no valid slot value" + (Object.keys(p.invalid).length ? " (rejected {" + Object.entries(p.invalid).map(([k, v]) => `'${k}': '${v}'`).join(", ") + "})" : "");
          return this.fallback(why, fmt(OUT.fallback, { hint: this.askNext() }));
        }
        return this.confirm(p);
      }
      confirm(p) {
        if (!this.hardened) return p.affirm ? this.complete("keyword YES found")
          : this.fallback("confirmation not affirmed", fmt(OUT.fallback, { hint: "Say yes or no." }));
        if (p.sarcasm) return this.fallback("sarcasm marker: affirmation not trusted",
          fmt(OUT.fallback, { hint: "Please answer plainly: yes or no." }), ["SARCASM"]);
        const corr = Object.fromEntries(Object.entries(p.slots).filter(([k, v]) => this.slot_memory[k] !== v));
        if (Object.keys(corr).length) { Object.assign(this.slot_memory, corr);
          return ["CONFIRMATION", fmt(OUT.confirm, this.slot_memory), "user corrected " + Object.keys(corr).join(", "), ["CORRECTION"]]; }
        if (p.affirm && p.negate) return this.fallback("contradictory affirmation", fmt(OUT.fallback, { hint: "Yes or no?" }));
        if (p.affirm) return this.complete("explicit affirmation");
        if (p.negate) return ["COLLECT_SLOTS", OUT.which_slot, "user rejected summary", []];
        return this.fallback("no yes/no in CONFIRMATION", fmt(OUT.fallback, { hint: "Yes or no?" }));
      }
      absorb(p) {
        let nw;
        if (this.hardened) nw = { ...p.slots };
        else { nw = {}; for (const k of this.missing().slice(0, 1)) if (k in p.slots) nw[k] = p.slots[k]; }
        Object.assign(this.slot_memory, nw);
        return nw;
      }
      after(rule) {
        this.fallback_count = 0;
        if (!this.missing().length) return ["CONFIRMATION", fmt(OUT.confirm, this.slot_memory), rule, []];
        return ["COLLECT_SLOTS", this.askNext(), rule, []];
      }
      askNext() { const m = this.missing(); return m.length ? OUT["ask_" + m[0]] : fmt(OUT.confirm, this.slot_memory); }
      hint() { if (this.resume === "INIT") return "Please greet first."; return this.resume === "COLLECT_SLOTS" ? this.askNext() : "Please rephrase."; }
      complete(rule) { this.outcome = "COMPLETED"; return ["TERMINAL", fmt(OUT.completed, this.slot_memory), rule, []]; }
      fallback(rule, output, flags = []) {
        this.fallback_count++;
        if (this.fallback_count >= this.maxRetries) { this.outcome = "ABORTED";
          return ["TERMINAL", OUT.aborted, `${rule}; max retries (${this.maxRetries}) reached`, flags.concat(["ABORT"])]; }
        return ["FALLBACK", output, rule, flags];
      }
    }
    const filled = (f) => Object.keys(f).length ? " (" + Object.entries(f).map(([k, v]) => `${k}=${v}`).join(", ") + ")" : "";

    /* ---- channel (Math.random noise source, same three rules as NoisyChannel.transmit) ---- */
    const norm = (w) => w.toLowerCase().replace(/[^a-z]/g, "");
    const isNoise = (w) => NOISE_SET.has(norm(w));
    const pick = (rnd) => NOISE[Math.floor(rnd() * NOISE.length)];
    function transmit(message, plasma = 0, rnd = Math.random, maxWords = spec.max_words) {
      const words = message.split(/\s+/).filter(Boolean);
      const compliant = words.length <= maxWords && words.some(isNoise);
      const cells = words.slice(0, maxWords).map((w) => ({ w, sent: w, kind: isNoise(w) ? "noise" : "kept" }));
      const truncated = words.slice(maxWords);
      let injected = null;
      if (!cells.some((c) => c.kind === "noise")) {
        injected = pick(rnd);
        if (cells.length < maxWords) cells.splice(Math.floor(rnd() * (cells.length + 1)), 0, { w: injected, sent: null, kind: "injected" });
        else { const i = cells.length > 1 ? 1 + Math.floor(rnd() * (cells.length - 1)) : 0; cells[i] = { w: injected, sent: cells[i].w, kind: "substituted" }; }
      }
      if (plasma > 0) for (const c of cells) if (c.kind === "kept" && rnd() < plasma) { c.kind = "substituted"; c.w = pick(rnd); }
      return { sent: message, cells, truncated, injected, compliant, received: cells.map((c) => c.w).join(" ") };
    }
    const STOP = new Set(spec.stopwords);
    const tokenize = (t) => t.toLowerCase().match(/[a-z0-9'][a-z0-9'-]*/g) || [];
    const entropy = (toks) => { if (!toks.length) return 0; const c = {}; toks.forEach((t) => c[t] = (c[t] || 0) + 1);
      return -Object.values(c).reduce((s, n) => s + (n / toks.length) * Math.log2(n / toks.length), 0); };
    const content = (t) => tokenize(t).filter((w) => !STOP.has(w) && !NOISE_SET.has(w));
    function metrics(rec, intent) {
      const toks = tokenize(rec.received), noise = toks.filter((t) => NOISE_SET.has(t)).length;
      const want = new Set(content(intent ?? rec.sent)), got = new Set(content(rec.received));
      return { words: toks.length, noise, payload: toks.length - noise, noise_ratio: toks.length ? noise / toks.length : 0,
               H: entropy(toks), survival: want.size ? [...want].filter((w) => got.has(w)).length / want.size : 1,
               lost: rec.truncated.length + rec.cells.filter((c) => c.kind === "substituted" && c.sent).length };
    }
    return { parse, stripNoise, FST, transmit, metrics, entropy, isNoise, NOISE };
  }

  const api = { create };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AuraFST = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
