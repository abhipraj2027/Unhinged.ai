const API = "https://unhinged.email";
let authToken = null;
let currentUserEmail = "";
let lastRewrite = "";
let lastRewriteSubject = "";

Office.onReady(() => {
  authToken = localStorage.getItem("unhinged_token");
  wireEvents();
  checkAuth();
});

function wireEvents() {
  document.getElementById("toSignup").onclick = (e) => { e.preventDefault(); showView("signupView"); };
  document.getElementById("toLogin").onclick = (e) => { e.preventDefault(); showView("loginView"); };
  document.getElementById("doLoginBtn").onclick = doLogin;
  document.getElementById("doSignupBtn").onclick = doSignup;
  document.getElementById("logoutLink").onclick = (e) => { e.preventDefault(); doLogout(); };
  document.getElementById("analyzeBtn").onclick = doAnalyze;
}

function showView(id) {
  ["loginView", "signupView", "mainView"].forEach(v => {
    document.getElementById(v).style.display = v === id ? "block" : "none";
  });
}

function showToast(msg, isError = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.style.display = "block";
  setTimeout(() => { t.style.display = "none"; }, 3500);
}

async function apiFetch(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const r = await fetch(`${API}${path}`, { ...opts, headers });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, data };
}

async function checkAuth() {
  if (!authToken) { showView("loginView"); return; }
  const { ok, data } = await apiFetch("/api/auth/me");
  if (ok && data.email) {
    currentUserEmail = data.email;
    document.getElementById("statusEmail").textContent = data.email;
    showView("mainView");
  } else {
    authToken = null;
    localStorage.removeItem("unhinged_token");
    showView("loginView");
  }
}

async function doLogin() {
  const email = document.getElementById("loginEmail").value.trim();
  const password = document.getElementById("loginPw").value;
  if (!email || !password) return showToast("Enter email and password", true);
  const { ok, data } = await apiFetch("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
  if (ok && data.token) {
    authToken = data.token;
    localStorage.setItem("unhinged_token", authToken);
    checkAuth();
  } else {
    showToast(data.detail || "Login failed", true);
  }
}

async function doSignup() {
  const email = document.getElementById("signupEmail").value.trim();
  const password = document.getElementById("signupPw").value;
  if (!email || password.length < 6) return showToast("Password must be 6+ characters", true);
  const { ok, data } = await apiFetch("/api/auth/signup", { method: "POST", body: JSON.stringify({ email, password }) });
  if (ok && data.token) {
    authToken = data.token;
    localStorage.setItem("unhinged_token", authToken);
    checkAuth();
  } else {
    showToast(data.detail || "Signup failed", true);
  }
}

function doLogout() {
  authToken = null;
  localStorage.removeItem("unhinged_token");
  document.getElementById("resultCard").style.display = "none";
  showView("loginView");
}

function getComposeBodyText() {
  return new Promise((resolve, reject) => {
    Office.context.mailbox.item.body.getAsync(Office.CoercionType.Text, (result) => {
      if (result.status === Office.AsyncResultStatus.Succeeded) resolve(result.value);
      else reject(result.error);
    });
  });
}

function setComposeBodyText(text) {
  return new Promise((resolve, reject) => {
    Office.context.mailbox.item.body.setAsync(text, { coercionType: Office.CoercionType.Text }, (result) => {
      if (result.status === Office.AsyncResultStatus.Succeeded) resolve();
      else reject(result.error);
    });
  });
}

function setComposeSubject(subject) {
  return new Promise((resolve, reject) => {
    Office.context.mailbox.item.subject.setAsync(subject, (result) => {
      if (result.status === Office.AsyncResultStatus.Succeeded) resolve();
      else reject(result.error);
    });
  });
}

async function doAnalyze() {
  const btn = document.getElementById("analyzeBtn");
  const card = document.getElementById("resultCard");
  btn.disabled = true;
  btn.textContent = "Analyzing...";
  card.style.display = "none";
  try {
    const message = await getComposeBodyText();
    if (!message || !message.trim()) {
      showToast("Write something first", true);
      return;
    }
    const { ok, status, data } = await apiFetch("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ email: currentUserEmail, message: message.trim() }),
    });
    if (status === 402) {
      renderPaywall(data);
      return;
    }
    if (!ok) {
      showToast(data.detail || data.message || "Analysis failed", true);
      return;
    }
    renderResult(data);
  } catch (e) {
    showToast("Something went wrong. Try again.", true);
  } finally {
    btn.disabled = false;
    btn.textContent = "🔥 Check This Email";
  }
}

function renderResult(d) {
  lastRewrite = d.rewrite || "";
  lastRewriteSubject = d.rewrite_subject || "";
  const card = document.getElementById("resultCard");
  card.innerHTML = `
    <div class="score-row">
      <span class="score-num">${d.score ?? "?"}</span>
      <span class="score-label">/ 10 unhinged</span>
    </div>
    <div class="section-label">Roast</div>
    <div class="roast-text">${escapeHtml(d.roast || "")}</div>
    <div class="section-label">Professional Rewrite</div>
    <div class="rewrite-text">${escapeHtml(d.rewrite || "")}</div>
    <button class="btn primary" id="replaceDraftBtn" style="margin-top:12px">Replace Draft</button>
  `;
  card.style.display = "block";
  document.getElementById("replaceDraftBtn").onclick = async () => {
    try {
      await setComposeBodyText(lastRewrite);
      if (lastRewriteSubject) {
        try { await setComposeSubject(lastRewriteSubject); }
        catch (e) { /* subject set can fail on read-only/reply threads — body replace still succeeded, don't block on it */ }
      }
      showToast("Draft replaced ✓");
    } catch (e) {
      showToast("Couldn't replace draft", true);
    }
  };
}

function renderPaywall(d) {
  const isPro = !!d.is_pro;
  const card = document.getElementById("resultCard");
  let html = `<div class="section-label">⚡ ${isPro ? "Daily limit reached" : "Free daily limit reached"}</div>`;
  if (isPro) {
    html += `<div class="hint">You've used all 30 Pro scans today. Resets at midnight UTC.</div>`;
  } else {
    html += `<div class="hint">Buy more scans below, or upgrade to Pro for 30 scans/day (₹299/month).</div>`;
    html += `<div class="pack-row">
      <button class="btn ghost" id="buySmall">30 — ₹49</button>
      <button class="btn ghost" id="buyMedium">100 — ₹149</button>
      <button class="btn ghost" id="buyLarge">250 — ₹299</button>
    </div>`;
    html += `<button class="btn primary" id="doUpgrade">Upgrade to Pro — ₹299/month</button>`;
  }
  card.innerHTML = html;
  card.style.display = "block";
  if (!isPro) {
    document.getElementById("buySmall").onclick = () => buyCredits("small");
    document.getElementById("buyMedium").onclick = () => buyCredits("medium");
    document.getElementById("buyLarge").onclick = () => buyCredits("large");
    document.getElementById("doUpgrade").onclick = doUpgrade;
  }
}

function openPaymentDialog(url) {
  // Office.js dialog API is the reliable cross-platform way to open external
  // payment pages — window.open is not consistently supported inside the
  // embedded webview Outlook desktop uses for task panes.
  Office.context.ui.displayDialogAsync(url, { height: 70, width: 50 }, (result) => {
    if (result.status !== Office.AsyncResultStatus.Succeeded) {
      console.error("displayDialogAsync failed:", result.error);
      showToast(`Couldn't open payment page (${result.error?.message || "unknown error"})`, true);
    }
  });
}

async function buyCredits(pack) {
  try {
    const { ok, data } = await apiFetch("/api/credits/checkout", {
      method: "POST",
      body: JSON.stringify({ pack, email: currentUserEmail }),
    });
    if (ok && data.payment_link) {
      openPaymentDialog(data.payment_link);
      showToast("Complete payment in the window that opened — credits apply automatically");
    } else {
      showToast(data.detail || "Checkout failed", true);
    }
  } catch (e) {
    console.error("buyCredits error:", e);
    showToast("Network error — see console for details", true);
  }
}

async function doUpgrade() {
  try {
    const { ok, data } = await apiFetch("/api/create-subscription", {
      method: "POST",
      body: JSON.stringify({ email: currentUserEmail }),
    });
    if (ok && data.payment_link) {
      openPaymentDialog(data.payment_link);
      showToast("Complete payment in the window that opened");
    } else {
      showToast(data.detail || "Checkout failed", true);
    }
  } catch (e) {
    console.error("doUpgrade error:", e);
    showToast("Network error — see console for details", true);
  }
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}
