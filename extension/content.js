// UnHinged content.js v3.0 — production ready
(function () {
  if (window.__unhingedInjected) return;
  window.__unhingedInjected = true;

  const MAX_CHARS = 2000;
  const LOADING_MSGS = [
    "Measuring toxicity levels...",
    "Consulting HR...",
    "Counting exclamation marks...",
    "Calculating your regret...",
    "Locating chill pills...",
    "Analyzing passive aggression...",
  ];

  let auth = { loggedIn: false, email: null };  // account (token-based)
  let guestEmail = null;                         // free-tier, no account
  let status = { is_pro: false, scans_remaining: 5, scans_used: 0, limit: 5, team: null };

  // ── Helpers ──────────────────────────────────────────────
  const send = (msg) =>
    new Promise((resolve) => {
      try { chrome.runtime.sendMessage(msg, resolve); }
      catch { resolve(null); }
    });

  function currentEmail() { return auth.loggedIn ? auth.email : guestEmail; }
  function teamName(s) {
    if (!s || !s.team) return null;
    return typeof s.team === "string" ? s.team : (s.team.name || null);
  }

  // ── Shadow DOM ───────────────────────────────────────────
  const host = document.createElement("div");
  host.id = "unhinged-host";
  host.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;pointer-events:none;overflow:visible;isolation:isolate;";
  document.documentElement.appendChild(host);
  const shadow = host.attachShadow({ mode: "open" });

  shadow.innerHTML = `<style>
*,*::before,*::after{box-sizing:border-box}
.fab{position:fixed;bottom:28px;right:28px;width:56px;height:56px;border-radius:50%;background:linear-gradient(135deg,#FF5C00,#FF3B30);color:#000;display:flex;align-items:center;justify-content:center;font-size:28px;cursor:pointer;box-shadow:0 8px 24px rgba(255,92,0,.5);border:2px solid rgba(0,0,0,.15);transition:transform .18s,box-shadow .18s;pointer-events:auto;user-select:none}
.fab:hover{transform:translateY(-3px) scale(1.05);box-shadow:0 14px 32px rgba(255,92,0,.6)}
.fab .badge{position:absolute;top:-2px;right:-2px;min-width:18px;height:18px;border-radius:50%;background:#22C55E;color:#000;font-size:10px;font-weight:800;display:flex;align-items:center;justify-content:center;border:2px solid #141416}
.panel{position:fixed;width:420px;max-height:min(88vh,720px);background:linear-gradient(180deg,rgba(20,20,22,.98),rgba(10,10,10,.98));color:#fff;border-radius:16px;box-shadow:0 32px 80px rgba(0,0,0,.8),0 0 0 1px rgba(255,255,255,.06),inset 0 1px 0 rgba(255,255,255,.05);backdrop-filter:blur(24px);display:flex;flex-direction:column;font-family:'Inter',-apple-system,sans-serif;overflow:hidden;opacity:0;transform:scale(.96) translateY(8px);transition:opacity .2s,transform .22s cubic-bezier(.22,1,.36,1);pointer-events:none;z-index:2147483647;isolation:isolate}
.panel.open{opacity:1;transform:scale(1) translateY(0);pointer-events:auto}
.panel::before{content:"";position:absolute;top:0;left:20px;right:20px;height:2px;background:linear-gradient(90deg,transparent,#FF5C00,transparent);opacity:.6}
header{padding:12px 14px;display:flex;align-items:center;justify-content:space-between;gap:10px;cursor:grab;border-bottom:1px solid rgba(255,255,255,.05)}
header:active{cursor:grabbing}
.brand{display:flex;align-items:center;gap:10px}
.logo{width:34px;height:34px;border-radius:8px;background:linear-gradient(135deg,#FF5C00,#FF3B30);display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 4px 10px rgba(255,92,0,.4)}
.brand h1{margin:0;font-size:14px;font-weight:900;letter-spacing:-.01em}
.brand .sub{margin-top:3px;font-size:9px;letter-spacing:.3em;text-transform:uppercase;color:#71717A;font-family:ui-monospace,monospace}
.hdr-acts{display:flex;gap:4px}
.ibtn{width:28px;height:28px;border-radius:6px;background:0;border:0;cursor:pointer;color:#71717A;display:flex;align-items:center;justify-content:center;font-size:14px;transition:background .15s,color .15s}
.ibtn:hover{background:rgba(255,255,255,.06);color:#FF5C00}
.body{padding:14px 16px 16px;overflow-y:auto;flex:1;scrollbar-width:thin;scrollbar-color:rgba(255,92,0,.3) transparent}
.body::-webkit-scrollbar{width:6px}.body::-webkit-scrollbar-thumb{background:rgba(255,92,0,.3);border-radius:3px}

/* Auth gate */
.gate-tabs{display:flex;gap:4px;margin-bottom:14px;background:rgba(255,255,255,.03);border-radius:10px;padding:3px}
.gate-tab{flex:1;padding:8px 4px;text-align:center;font-size:10.5px;letter-spacing:.05em;font-weight:700;color:#71717A;background:0;border:0;border-radius:7px;cursor:pointer;transition:background .15s,color .15s}
.gate-tab.active{background:linear-gradient(135deg,#FF5C00,#FF3B30);color:#000}
.gate-form{display:none}
.gate-form.active{display:block}
.gate-form p{font-size:12px;color:#A1A1AA;margin:0 0 12px;line-height:1.5}
.gate-form label{display:block;font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:#71717A;margin-bottom:5px;font-family:ui-monospace,monospace}
.gate-form input{width:100%;background:rgba(0,0,0,.4);color:#fff;border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:11px 12px;font-size:13px;outline:0;margin-bottom:10px;font-family:ui-monospace,monospace}
.gate-form input:focus{border-color:#FF5C00}
.gate-err{font-size:11px;color:#FCA5A5;margin:-4px 0 10px;display:none}
.gate-err.show{display:block}
.gate-submit{width:100%;padding:13px;background:linear-gradient(135deg,#FF5C00,#FF3B30);color:#000;border:0;border-radius:10px;cursor:pointer;font-size:13px;font-weight:800;letter-spacing:.05em}
.gate-submit:disabled{opacity:.6;cursor:wait}
.gate-alt{text-align:center;margin-top:12px;font-size:11px;color:#52525B}
.gate-alt a{color:#22C55E;text-decoration:none;cursor:pointer}

/* Account bar (shown above textarea when logged in / guest) */
.acct-bar{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;margin-bottom:10px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:8px;font-size:10.5px;font-family:ui-monospace,monospace;color:#A1A1AA}
.acct-bar b{color:#F4F4F5}
.acct-bar a{color:#FF5C00;text-decoration:none;cursor:pointer;font-weight:700}

/* Trial bar */
.trial-bar{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;margin-bottom:12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:10px}
.dots{display:flex;gap:5px}
.dot{width:10px;height:10px;border-radius:50%;border:1.5px solid rgba(255,92,0,.5);background:0;transition:background .3s,border-color .3s}
.dot.used{background:rgba(255,92,0,.15);border-color:rgba(255,92,0,.2)}
.dot.avail{background:#FF5C00;border-color:#FF5C00;box-shadow:0 0 6px rgba(255,92,0,.4)}
.tlab{font-size:11px;color:#A1A1AA;font-family:ui-monospace,monospace}
.tlab b{color:#FF5C00}
.tlab.zero b{color:#EF4444}
.pill{display:inline-flex;padding:2px 7px;border-radius:999px;font-size:9px;letter-spacing:.15em;text-transform:uppercase;font-family:ui-monospace,monospace}
.pill-pro{background:linear-gradient(135deg,#FF5C00,#FF3B30);color:#000;font-weight:700}
.pill-free{background:rgba(255,255,255,.06);color:#A1A1AA}

/* Textarea + buttons */
.field-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.lbl{font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:#71717A;font-family:ui-monospace,monospace}
.cc{font-size:10px;color:#71717A;font-family:ui-monospace,monospace}
.cc.w{color:#FACC15}.cc.e{color:#EF4444}
textarea{width:100%;min-height:96px;max-height:160px;resize:vertical;background:rgba(0,0,0,.4);color:#fff;border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:10px 12px;font-family:ui-monospace,monospace;font-size:13px;line-height:1.5;outline:0;transition:border-color .15s}
textarea::placeholder{color:#52525B}
textarea:focus{border-color:#FF5C00;box-shadow:0 0 0 2px rgba(255,92,0,.15)}
.btn-grab{margin-top:10px;width:100%;background:0;color:#A1A1AA;border:1px dashed rgba(255,255,255,.15);padding:10px;border-radius:8px;cursor:pointer;font-family:ui-monospace,monospace;font-size:11px;letter-spacing:.15em;text-transform:uppercase;display:inline-flex;align-items:center;justify-content:center;gap:8px;transition:color .15s,border-color .15s}
.btn-grab:hover{color:#FF5C00;border-color:#FF5C00}
.btn-go{margin-top:10px;width:100%;padding:14px;background:linear-gradient(135deg,#FF5C00,#FF3B30);color:#000;border:0;border-radius:10px;cursor:pointer;font-size:13px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;display:inline-flex;align-items:center;justify-content:center;gap:8px;box-shadow:0 6px 18px rgba(255,92,0,.35);transition:transform .12s,box-shadow .15s}
.btn-go:hover{transform:translateY(-2px);box-shadow:0 10px 24px rgba(255,92,0,.5)}
.btn-go:disabled{background:rgba(255,255,255,.06);color:#52525B;box-shadow:none;cursor:not-allowed;transform:none}

/* Loading */
.loading{margin-top:16px;padding:24px;border-radius:12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);text-align:center}
.spinner{width:32px;height:32px;border:2px solid #FF5C00;border-top-color:transparent;border-radius:50%;margin:0 auto 12px;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* Results */
.warn-ban{margin-top:14px;padding:12px 14px;border-radius:10px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.35);font-size:12px;line-height:1.5;color:#FCA5A5;display:flex;gap:10px;font-family:ui-monospace,monospace}
.gauge{text-align:center;padding:18px 0 6px}
.score{font-size:68px;line-height:1;font-weight:900;letter-spacing:-.03em}
.score .of{color:#71717A;font-size:22px;margin-left:6px}
.flame{display:inline-block;font-size:34px;margin-left:6px;animation:flick 1.2s ease-in-out infinite;filter:drop-shadow(0 0 10px rgba(255,92,0,.7))}
@keyframes flick{0%,100%{transform:scale(1) rotate(-2deg)}50%{transform:scale(1.1) rotate(3deg)}}
.track{margin:10px auto 4px;width:80%;height:6px;border-radius:999px;background:rgba(255,255,255,.06);overflow:hidden}
.track>i{display:block;height:100%;background:#FF5C00;transition:width .8s cubic-bezier(.22,1,.36,1);border-radius:999px}
.caption{font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:#71717A;margin-top:6px;font-family:ui-monospace,monospace}
.card{margin-top:12px;padding:12px 14px;border-radius:10px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06)}
.card .cap{font-size:10px;letter-spacing:.25em;text-transform:uppercase;color:#FF5C00;margin-bottom:8px;font-family:ui-monospace,monospace}
.card .cap.y{color:#FACC15}
.card p{margin:0;font-size:13px;line-height:1.55;color:#E4E4E7}
.rw-body{white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:12.5px;line-height:1.6;color:#F4F4F5}
.card.rw{background:rgba(0,0,0,.35);border-color:rgba(255,92,0,.15)}
.cap-row{display:flex;justify-content:space-between;align-items:center}
.mini{background:0;border:1px solid rgba(255,255,255,.1);color:#A1A1AA;padding:5px 10px;border-radius:6px;font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.15em;text-transform:uppercase;cursor:pointer;transition:color .15s,border-color .15s}
.mini:hover{color:#FF5C00;border-color:#FF5C00}
.mini.pri{color:#FF5C00;border-color:rgba(255,92,0,.5)}

/* CTA */
.cta-card{margin-top:14px;padding:16px;border-radius:12px;background:rgba(255,92,0,.06);border:1px solid rgba(255,92,0,.3);display:flex;flex-direction:column;gap:10px}
.cta-card.warn{background:rgba(239,68,68,.06);border-color:rgba(239,68,68,.3)}
.cta-title{font-size:14px;font-weight:700}
.cta-desc{font-size:12px;line-height:1.55;color:#D4D4D8;font-family:ui-monospace,monospace}
.cta-desc b{color:#FF5C00;font-weight:600}
.cta-btn{background:linear-gradient(135deg,#FF5C00,#FF3B30);color:#000;padding:11px 14px;text-align:center;border-radius:8px;font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;text-decoration:none;cursor:pointer;border:0;box-shadow:0 4px 12px rgba(255,92,0,.3);transition:transform .12s,box-shadow .15s}
.cta-btn:hover{transform:translateY(-1px);box-shadow:0 6px 16px rgba(255,92,0,.45)}
.cta-btn.ghost{background:0;color:#F4F4F5;border:1px solid rgba(255,255,255,.12);box-shadow:none}

/* Toast */
.toast{position:absolute;bottom:16px;left:50%;transform:translateX(-50%) translateY(20px);background:rgba(0,0,0,.9);backdrop-filter:blur(8px);color:#fff;border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:8px 14px;font-size:11px;letter-spacing:.15em;text-transform:uppercase;opacity:0;transition:all .2s;pointer-events:none;white-space:nowrap;font-family:ui-monospace,monospace}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.toast.err{border-color:rgba(239,68,68,.5);color:#FCA5A5}

footer{padding:10px 14px;border-top:1px solid rgba(255,255,255,.05);font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:#52525B;display:flex;justify-content:space-between;font-family:ui-monospace,monospace}
footer a{color:#71717A;text-decoration:none;cursor:pointer}
footer a:hover{color:#FF5C00}
</style>

<button class="fab" id="fab" title="UnHinged">🔥<span class="badge" id="fabBadge" style="display:none"></span></button>

<aside class="panel" id="panel">
  <header id="dragH">
    <div class="brand"><div class="logo">🔥</div><div><h1>UnHinged</h1><div class="sub">Email Tone Checker</div></div></div>
    <div class="hdr-acts"><button class="ibtn" id="minBtn">—</button><button class="ibtn" id="closeBtn">✕</button></div>
  </header>
  <div class="body" id="body">
    <!-- Auth gate (shown first time / after logout) -->
    <div id="gate">
      <div class="gate-tabs">
        <button class="gate-tab active" data-tab="login">Login</button>
        <button class="gate-tab" data-tab="signup">Sign Up</button>
        <button class="gate-tab" data-tab="join">Join Team</button>
        <button class="gate-tab" data-tab="guest">Guest</button>
      </div>

      <div class="gate-form active" data-form="login">
        <p>Log in to sync your Pro status and team across devices.</p>
        <label>Email</label>
        <input type="email" id="loginEmail" placeholder="you@company.com">
        <label>Password</label>
        <input type="password" id="loginPassword" placeholder="••••••••">
        <div class="gate-err" id="loginErr"></div>
        <button class="gate-submit" id="loginBtn">Login</button>
        <div class="gate-alt">Forgot password? <a id="forgotLink">Reset it</a></div>
      </div>

      <div class="gate-form" data-form="signup">
        <p>Create a free UnHinged account — 5 scans/day, upgrade anytime.</p>
        <label>Email</label>
        <input type="email" id="signupEmail" placeholder="you@company.com">
        <label>Password</label>
        <input type="password" id="signupPassword" placeholder="min 6 characters">
        <div class="gate-err" id="signupErr"></div>
        <button class="gate-submit" id="signupBtn">Sign Up</button>
      </div>

      <div class="gate-form" data-form="join">
        <p>Have a team invite code? Join and get Pro instantly.</p>
        <label>Email</label>
        <input type="email" id="joinEmail" placeholder="you@company.com">
        <label>Password</label>
        <input type="password" id="joinPassword" placeholder="set a password">
        <label>Invite Code</label>
        <input type="text" id="joinCode" placeholder="ABCD1234" style="text-transform:uppercase;letter-spacing:.15em">
        <div class="gate-err" id="joinErr"></div>
        <button class="gate-submit" id="joinBtn">Join Team</button>
      </div>

      <div class="gate-form" data-form="guest">
        <p>Skip the account — just track free scans by email. You won't get team features or synced Pro status.</p>
        <label>Email</label>
        <input type="email" id="guestEmailIn" placeholder="you@company.com">
        <div class="gate-err" id="guestErr"></div>
        <button class="gate-submit" id="guestBtn">🔥 Start Scanning (Free)</button>
      </div>
    </div>

    <!-- Main UI (shown after auth) -->
    <div id="mainUI" style="display:none">
      <div class="acct-bar" id="acctBar"></div>
      <div class="trial-bar" id="trialBar"><div style="display:flex;align-items:center;gap:10px"><div class="dots" id="dots"></div><div class="tlab" id="tlab"></div></div><span id="tpill"></span></div>
      <div class="field-row"><span class="lbl">Your draft</span><span class="cc" id="cc">0/2000</span></div>
      <textarea id="msg" placeholder="Paste the message you're about to regret — or grab it from your open Gmail draft."></textarea>
      <button class="btn-grab" id="grab">↓ Grab from Gmail compose</button>
      <button class="btn-go" id="goBtn" disabled><span>🔥</span><span>How Unhinged Am I?</span></button>
      <div id="results"></div>
    </div>
  </div>
  <footer>
    <span>v3.0 · brutally honest</span>
    <a id="upLink" style="color:#FF5C00;font-weight:700;cursor:pointer">⚡ Upgrade Pro</a>
  </footer>
  <div class="toast" id="toast"></div>
</aside>`;

  // ── DOM refs ─────────────────────────────────────────────
  const $ = (s) => shadow.querySelector(s);
  const fab = $("#fab"), fabBadge = $("#fabBadge"), panel = $("#panel");
  const closeBtn = $("#closeBtn"), minBtn = $("#minBtn"), dragH = $("#dragH");
  const gate = $("#gate");
  const mainUI = $("#mainUI"), acctBar = $("#acctBar"), trialBar = $("#trialBar"), dots = $("#dots"), tlab = $("#tlab"), tpill = $("#tpill");
  const msg = $("#msg"), cc = $("#cc"), grab = $("#grab"), goBtn = $("#goBtn");
  const results = $("#results"), toast = $("#toast"), upLink = $("#upLink");
  let userMoved = false;

  // ── Init: check account login, then guest email ──────────
  (async function init() {
    const a = await send({ action: "getAuth" });
    if (a?.loggedIn) {
      auth = { loggedIn: true, email: a.email };
      return showMainUI();
    }
    const g = await send({ action: "getEmail" });
    if (g?.email) {
      guestEmail = g.email;
      return showMainUI();
    }
    // else: gate stays visible on the default "Login" tab
  })();

  // ── Gate tabs ────────────────────────────────────────────
  shadow.querySelectorAll(".gate-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      shadow.querySelectorAll(".gate-tab").forEach((t) => t.classList.remove("active"));
      shadow.querySelectorAll(".gate-form").forEach((f) => f.classList.remove("active"));
      tab.classList.add("active");
      shadow.querySelector(`.gate-form[data-form="${tab.dataset.tab}"]`).classList.add("active");
    });
  });

  function gateError(id, text) {
    const el = shadow.getElementById(id);
    el.textContent = text;
    el.classList.add("show");
  }
  function clearGateError(id) {
    const el = shadow.getElementById(id);
    el.classList.remove("show");
  }

  // ── Login ────────────────────────────────────────────────
  $("#loginBtn").addEventListener("click", async () => {
    clearGateError("loginErr");
    const email = $("#loginEmail").value.trim().toLowerCase();
    const password = $("#loginPassword").value;
    if (!email || !email.includes("@")) return gateError("loginErr", "Enter a valid email");
    if (!password) return gateError("loginErr", "Enter your password");
    const btn = $("#loginBtn"); btn.disabled = true; btn.textContent = "Logging in...";
    const r = await send({ action: "login", email, password });
    btn.disabled = false; btn.textContent = "Login";
    if (!r || r.error) return gateError("loginErr", r?.message || "Login failed");
    auth = { loggedIn: true, email: r.email || email };
    showMainUI();
  });

  // ── Sign up ──────────────────────────────────────────────
  $("#signupBtn").addEventListener("click", async () => {
    clearGateError("signupErr");
    const email = $("#signupEmail").value.trim().toLowerCase();
    const password = $("#signupPassword").value;
    if (!email || !email.includes("@")) return gateError("signupErr", "Enter a valid email");
    if (password.length < 6) return gateError("signupErr", "Password must be at least 6 characters");
    const btn = $("#signupBtn"); btn.disabled = true; btn.textContent = "Creating account...";
    const r = await send({ action: "signup", email, password });
    btn.disabled = false; btn.textContent = "Sign Up";
    if (!r || r.error) return gateError("signupErr", r?.message || "Signup failed");
    auth = { loggedIn: true, email: r.email || email };
    showMainUI();
  });

  // ── Join team ────────────────────────────────────────────
  $("#joinBtn").addEventListener("click", async () => {
    clearGateError("joinErr");
    const email = $("#joinEmail").value.trim().toLowerCase();
    const password = $("#joinPassword").value;
    const inviteCode = $("#joinCode").value.trim().toUpperCase();
    if (!email || !email.includes("@")) return gateError("joinErr", "Enter a valid email");
    if (password.length < 6) return gateError("joinErr", "Password must be at least 6 characters");
    if (!inviteCode) return gateError("joinErr", "Enter your team's invite code");
    const btn = $("#joinBtn"); btn.disabled = true; btn.textContent = "Joining...";
    const r = await send({ action: "joinTeam", email, password, inviteCode });
    btn.disabled = false; btn.textContent = "Join Team";
    if (!r || r.error) return gateError("joinErr", r?.message || "Couldn't join team");
    auth = { loggedIn: true, email };
    showToast(`Joined ${r.team_name || "your team"} 🔥`);
    showMainUI();
  });

  // ── Guest ────────────────────────────────────────────────
  $("#guestBtn").addEventListener("click", () => {
    clearGateError("guestErr");
    const em = $("#guestEmailIn").value.trim().toLowerCase();
    if (!em || !em.includes("@")) return gateError("guestErr", "Enter a valid email");
    guestEmail = em;
    send({ action: "saveEmail", email: em });
    showMainUI();
  });
  $("#guestEmailIn").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#guestBtn").click(); });
  $("#loginPassword").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#loginBtn").click(); });
  $("#signupPassword").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#signupBtn").click(); });
  $("#joinCode").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#joinBtn").click(); });

  $("#forgotLink").addEventListener("click", () => {
    window.open("https://unhinged.email/login", "_blank", "noopener");
  });

  async function showMainUI() {
    gate.style.display = "none";
    mainUI.style.display = "block";
    const s = await send({ action: "checkStatus", email: currentEmail() });
    if (s) status = s;
    renderAcctBar();
    renderTrialBar();
    autoGrab();
  }

  function renderAcctBar() {
    if (auth.loggedIn) {
      const tn = teamName(status);
      let html = "👤 <b>" + esc(auth.email) + "</b>";
      if (tn) {
        html += " · 👥 <b>" + esc(tn) + "</b> <a id=\"teamLink\">Dashboard →</a>";
      } else {
        html += " <a id=\"acctLink\">Account →</a>";
      }
      html += " <a id=\"logoutLink\" style=\"color:#71717A;font-weight:400\">Logout</a>";
      acctBar.innerHTML = html;
      const teamLink = shadow.getElementById("teamLink");
      if (teamLink) teamLink.addEventListener("click", () => window.open("https://unhinged.email/teams", "_blank", "noopener"));
      const acctLink = shadow.getElementById("acctLink");
      if (acctLink) acctLink.addEventListener("click", () => window.open("https://unhinged.email/account", "_blank", "noopener"));
      shadow.getElementById("logoutLink").addEventListener("click", async () => {
        await send({ action: "logout" });
        auth = { loggedIn: false, email: null };
        mainUI.style.display = "none";
        gate.style.display = "block";
        showToast("Logged out");
      });
    } else {
      acctBar.innerHTML = "🔓 Guest: <b>" + esc(guestEmail) + "</b> <a id=\"loginSwitch\">Login for team access →</a>";
      shadow.getElementById("loginSwitch").addEventListener("click", () => {
        mainUI.style.display = "none";
        gate.style.display = "block";
      });
    }
  }

  // ── Compose detection ────────────────────────────────────
  function findCompose() {
    const bodies = document.querySelectorAll('div[aria-label="Message Body"][contenteditable="true"]');
    for (const b of bodies) { if (b.offsetParent !== null) return b; }
    return null;
  }

  function autoGrab() {
    if (msg.value.trim()) return;
    const el = findCompose();
    if (!el) return;
    const t = (el.innerText || "").trim();
    if (t && t.length < MAX_CHARS + 200) {
      msg.value = t.slice(0, MAX_CHARS + 50);
      msg.dispatchEvent(new Event("input"));
    }
  }

  // ── Panel positioning ────────────────────────────────────
  function anchorPanel() {
    if (userMoved) return;
    const W = 420, m = 16;
    let left = window.innerWidth - W - 28;
    let top = Math.max(80, (window.innerHeight - 500) / 2);
    const compose = findCompose();
    if (compose) {
      let dialog = compose;
      while (dialog && dialog !== document.body) {
        if (dialog.getAttribute?.("role") === "dialog") break;
        dialog = dialog.parentElement;
      }
      if (dialog && dialog !== document.body) {
        const r = dialog.getBoundingClientRect();
        if (r.left > W + m * 2) { left = r.left - W - m; top = r.top; }
        else if (r.right + W + m * 2 < window.innerWidth) { left = r.right + m; top = r.top; }
      }
    }
    left = Math.max(m, Math.min(left, window.innerWidth - W - m));
    top = Math.max(m, Math.min(top, window.innerHeight - 200));
    panel.style.left = left + "px";
    panel.style.top = top + "px";
  }

  // ── Toggle panel ─────────────────────────────────────────
  function toggle(open) {
    const show = open === undefined ? !panel.classList.contains("open") : open;
    if (show) {
      anchorPanel();
      requestAnimationFrame(() => panel.classList.add("open"));
    } else {
      panel.classList.remove("open");
    }
  }
  fab.addEventListener("click", () => toggle());
  closeBtn.addEventListener("click", () => toggle(false));
  minBtn.addEventListener("click", () => {
    const body = $("#body");
    const footer = shadow.querySelector("footer");
    if (body.style.display === "none") {
      body.style.display = ""; footer.style.display = "";
      minBtn.textContent = "—";
    } else {
      body.style.display = "none"; footer.style.display = "none";
      minBtn.textContent = "▢";
    }
  });

  chrome.runtime?.onMessage.addListener((m) => {
    if (m?.type === "UNHINGED_TOGGLE_PANEL") toggle();
  });

  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === "U") { e.preventDefault(); toggle(); }
  });

  // ── Drag ─────────────────────────────────────────────────
  (function () {
    let sx, sy, ox, oy, dragging = false;
    dragH.addEventListener("mousedown", (e) => {
      if (e.target.closest(".ibtn")) return;
      dragging = true; userMoved = true;
      sx = e.clientX; sy = e.clientY;
      const r = panel.getBoundingClientRect();
      ox = r.left; oy = r.top;
      e.preventDefault();
      e.stopPropagation();
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      e.preventDefault();
      panel.style.left = Math.max(4, Math.min(window.innerWidth - 60, ox + e.clientX - sx)) + "px";
      panel.style.top = Math.max(4, Math.min(window.innerHeight - 60, oy + e.clientY - sy)) + "px";
    }, true);
    window.addEventListener("mouseup", () => { if (dragging) dragging = false; }, true);
  })();

  // ── Trial bar ────────────────────────────────────────────
  function renderTrialBar() {
    if (status.is_pro) {
      dots.innerHTML = "";
      tlab.className = "tlab";
      const rem = status.scans_remaining ?? 30;
      tlab.innerHTML = "<b>" + rem + "</b>/30 scans today";
      tpill.innerHTML = '<span class="pill pill-pro">PRO</span>';
      fabBadge.style.display = "none";
      updateGoBtn();
      return;
    }
    const rem = status.scans_remaining ?? (status.limit - (status.daily_scans || status.scans_used));
    const lim = status.daily_limit || status.limit || 5;
    let d = "";
    for (let i = 0; i < lim; i++) {
      d += '<div class="dot ' + (i < rem ? "avail" : "used") + '"></div>';
    }
    dots.innerHTML = d;
    tpill.innerHTML = '<span class="pill pill-free">FREE</span>';
    if (rem > 0) {
      tlab.className = "tlab";
      tlab.innerHTML = "<b>" + rem + "</b>/" + lim + " scans today";
    } else {
      tlab.className = "tlab zero";
      tlab.innerHTML = "<b>Daily limit reached</b>";
    }
    if (rem > 0 && rem < lim) { fabBadge.textContent = rem; fabBadge.style.display = "flex"; }
    else fabBadge.style.display = "none";
    updateGoBtn();
  }

  // ── Textarea ─────────────────────────────────────────────
  function updateGoBtn() {
    const n = msg.value.length;
    const rem = status.is_pro ? 999 : (status.scans_remaining ?? 5);
    if (rem <= 0 && !status.is_pro) {
      goBtn.disabled = false;
      goBtn.innerHTML = "<span>⚡</span><span>Upgrade to Pro</span>";
    } else {
      goBtn.disabled = n === 0 || n > MAX_CHARS;
      goBtn.innerHTML = '<span>🔥</span><span>How Unhinged Am I?</span>';
    }
  }
  msg.addEventListener("input", () => {
    if (msg.value.length > MAX_CHARS + 50) msg.value = msg.value.slice(0, MAX_CHARS + 50);
    const n = msg.value.length;
    cc.textContent = n + "/" + MAX_CHARS;
    cc.className = n > MAX_CHARS ? "cc e" : n > MAX_CHARS * 0.9 ? "cc w" : "cc";
    updateGoBtn();
  });

  // ── Grab from compose ────────────────────────────────────
  grab.addEventListener("click", () => {
    const el = findCompose();
    if (!el) return showToast("No open compose window", true);
    const t = (el.innerText || "").trim();
    if (!t) return showToast("Compose is empty", true);
    msg.value = t.slice(0, MAX_CHARS + 50);
    msg.dispatchEvent(new Event("input"));
    showToast("Draft grabbed ✓");
  });

  // ── Analyze ──────────────────────────────────────────────
  goBtn.addEventListener("click", async () => {
    const rem = status.is_pro ? 999 : (status.scans_remaining ?? 5);
    if (rem <= 0 && !status.is_pro) return doUpgrade();

    const text = msg.value.trim();
    if (!text) return;

    renderLoading();
    const r = await send({ action: "analyze", email: currentEmail(), message: text });

    if (!r || r.error) {
      if (r?.trialEnded || r?.error === "limit_reached") return renderPaywall(status.is_pro);
      return renderError(r?.message || r?.detail || "Something broke. Try again.");
    }

    status.scans_used = r.scans_used ?? status.scans_used;
    status.scans_remaining = r.scans_remaining ?? Math.max(0, (status.limit || 5) - (r.scans_used || 0));
    status.is_pro = r.is_pro ?? status.is_pro;
    renderTrialBar();
    renderResults(r);
  });

  // ── Renderers ────────────────────────────────────────────
  let loadingTimer = null;
  function renderLoading() {
    if (loadingTimer) clearInterval(loadingTimer);
    let i = 0;
    results.innerHTML = '<div class="loading"><div class="spinner"></div><div id="lm" style="font-family:ui-monospace,monospace;font-size:12px;color:#A1A1AA">' + LOADING_MSGS[0] + '</div></div>';
    loadingTimer = setInterval(() => {
      i = (i + 1) % LOADING_MSGS.length;
      const el = shadow.getElementById("lm");
      if (el) el.textContent = LOADING_MSGS[i];
    }, 1400);
  }
  function clearLoading() { if (loadingTimer) { clearInterval(loadingTimer); loadingTimer = null; } }

  function scoreColor(s) { return s < 4 ? "#22C55E" : s < 8 ? "#FACC15" : "#EF4444"; }

  function renderResults(d) {
    clearLoading();
    const s = Math.max(1, Math.min(10, Number(d.score) || 0));
    const col = scoreColor(s);
    const danger = s >= 8;
    let html = "";
    if (danger) {
      html += '<div class="warn-ban"><span>⚠️</span><div><strong style="color:#EF4444;letter-spacing:.15em;text-transform:uppercase;font-size:11px">Reconsider before sending</strong>';
      html += '<div style="margin-top:4px;color:#F4F4F5">' + s.toFixed(1) + '/10. Apply the calm rewrite below.</div></div></div>';
    }
    html += '<div class="gauge">';
    html += '<div class="score" style="color:' + col + ';text-shadow:0 0 24px ' + col + '55">' + s.toFixed(1) + '<span class="of">/10</span>' + (danger ? '<span class="flame">🔥</span>' : "") + '</div>';
    html += '<div class="track"><i style="width:' + (s / 10) * 100 + '%;background:' + col + '"></i></div>';
    html += '<div class="caption">Unhinged Score</div>';
    html += '</div>';
    html += '<div class="card"><div class="cap">▲ AI Roast</div><p id="roastP"></p></div>';
    html += '<div class="card" style="border-left:3px solid #FACC15"><div class="cap y">Risk Assessment</div><p id="riskP" style="font-family:ui-monospace,monospace;font-size:12.5px"></p></div>';
    html += '<div class="card rw"><div class="cap-row"><div class="cap">Professional Rewrite</div><div style="display:flex;gap:6px"><button class="mini" id="cpRw">Copy</button><button class="mini pri" id="apRw">↳ Replace draft</button></div></div>';
    html += '<div class="rw-body" id="rwBody" style="margin-top:10px"></div></div>';
    results.innerHTML = html;
    shadow.getElementById("roastP").textContent = d.roast || "";
    shadow.getElementById("riskP").textContent = d.risk || "";
    shadow.getElementById("rwBody").textContent = d.rewrite || "";
    shadow.getElementById("cpRw").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(d.rewrite || ""); showToast("Copied ✓"); }
      catch { showToast("Copy failed", true); }
    });
    shadow.getElementById("apRw").addEventListener("click", () => {
      const el = findCompose();
      if (!el) return showToast("No open compose window", true);
      el.innerHTML = esc(d.rewrite || "").replace(/\n/g, "<br>");
      el.dispatchEvent(new InputEvent("input", { bubbles: true }));
      showToast("Draft replaced ✓");
    });
  }

  function renderError(m) {
    clearLoading();
    results.innerHTML = '<div class="cta-card warn"><div class="cta-title">✕ Analysis failed</div><div class="cta-desc" id="errMsg"></div></div>';
    shadow.getElementById("errMsg").textContent = m;
  }

  function renderPaywall(isPro = false) {
    clearLoading();
    const title = isPro ? "Daily limit reached" : "Free daily limit reached";
    const desc = isPro
      ? "You've used all <b>30</b> Pro scans today.<br>Resets at <b>midnight UTC</b>."
      : "You've used all <b>5</b> free scans today.<br>Upgrade to <b>UnHinged Pro</b> for <b>30 scans/day</b>.<br><span style=\"color:#71717A\">₹199/month · Cancel anytime</span>";
    let html = '<div class="cta-card">';
    html += '<div class="cta-title">⚡ ' + title + '</div>';
    html += '<div class="cta-desc">' + desc + '</div>';
    if (!isPro) html += '<button class="cta-btn" id="doUp">Upgrade to Pro — ₹199/month</button>';
    if (!isPro) html += '<button class="cta-btn ghost" id="chkPro">I just upgraded — restore access</button>';
    html += '</div>';
    results.innerHTML = html;
    shadow.getElementById("doUp")?.addEventListener("click", doUpgrade);
    shadow.getElementById("chkPro")?.addEventListener("click", async () => {
      const s = await send({ action: "checkStatus", email: currentEmail() });
      if (s) status = s;
      renderTrialBar();
      if (status.is_pro) { results.innerHTML = ""; showToast("Welcome to Pro 🔥"); }
      else showToast("Still on free plan", true);
    });
  }

  async function doUpgrade() {
    showToast("Opening payment...");
    const r = await send({ action: "createSubscription", email: currentEmail() });
    if (r?.already_pro) {
      status.is_pro = true;
      renderTrialBar();
      results.innerHTML = "";
      return showToast("You're already Pro! 🔥");
    }
    if (r?.payment_link) {
      window.open(r.payment_link, "_blank", "noopener");
      showToast("Complete payment in the new tab");
    } else {
      showToast(r?.message || "Payment error", true);
    }
  }
  upLink.addEventListener("click", (e) => { e.preventDefault(); doUpgrade(); });

  // ── Utils ────────────────────────────────────────────────
  let toastTimer = null;
  function showToast(t, err = false) {
    toast.textContent = t;
    toast.className = "toast show" + (err ? " err" : "");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 2400);
  }
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
})();
