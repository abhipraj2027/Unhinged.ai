// ═══════════════════════════════════════════════════════════
// UnHinged background.js v3.0
// ═══════════════════════════════════════════════════════════
const BACKEND = "https://unhinged.email";

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab?.id || !tab.url?.startsWith("https://mail.google.com/")) {
    chrome.action.setBadgeBackgroundColor({ color: "#FF5C00" });
    chrome.action.setBadgeText({ text: "!", tabId: tab.id });
    setTimeout(() => chrome.action.setBadgeText({ text: "", tabId: tab.id }), 2500);
    return;
  }
  chrome.tabs.sendMessage(tab.id, { type: "UNHINGED_TOGGLE_PANEL" });
});

async function apiFetch(path, opts = {}) {
  const { token } = await chrome.storage.local.get(["authToken"]);
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const r = await fetch(`${BACKEND}${path}`, { ...opts, headers });
  let data = {};
  try { data = await r.json(); } catch {}
  return { status: r.status, data };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // ── Guest-mode analyze (works whether logged in or not; email
  //    is either the account email or the free-tier typed email) ──
  if (msg.action === "analyze") {
    apiFetch("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ email: msg.email, message: msg.message }),
    })
      .then(({ status, data }) => {
        if (status === 402) sendResponse({ error: true, trialEnded: true, ...data });
        else if (status >= 400) sendResponse({ error: true, message: data.detail || data.error || data.message || "Analysis failed" });
        else sendResponse({ error: false, ...data });
      })
      .catch((e) => sendResponse({ error: true, message: e.message || "Network error" }));
    return true;
  }

  // ── Status: prefer authenticated /api/auth/me (Bearer token) when
  //    logged in — it's authoritative and includes team info; fall back
  //    to the unauthenticated per-email check for guest mode ──
  if (msg.action === "checkStatus") {
    chrome.storage.local.get(["authToken"], async ({ authToken }) => {
      if (authToken) {
        const { status, data } = await apiFetch("/api/auth/me");
        if (status === 200) return sendResponse(data);
        // token expired/invalid — drop it and fall back to guest
        await chrome.storage.local.remove(["authToken", "authEmail"]);
      }
      fetch(`${BACKEND}/api/check-status?email=${encodeURIComponent(msg.email || "")}`)
        .then((r) => r.json())
        .then((d) => sendResponse(d))
        .catch(() => sendResponse({ is_pro: false, scans_remaining: 5 }));
    });
    return true;
  }

  if (msg.action === "createSubscription") {
    apiFetch("/api/create-subscription", {
      method: "POST",
      body: JSON.stringify({ email: msg.email }),
    })
      .then(({ data }) => sendResponse(data))
      .catch((e) => sendResponse({ error: true, message: e.message }));
    return true;
  }

  // ── Auth: login / signup / join-team all return {token, ...profile};
  //    store the token so subsequent requests are properly authenticated
  //    instead of just trusting a typed email. ──
  if (msg.action === "login") {
    apiFetch("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: msg.email, password: msg.password }),
    }).then(async ({ status, data }) => {
      if (status !== 200 || !data.token) return sendResponse({ error: true, message: data.detail || "Login failed" });
      await chrome.storage.local.set({ authToken: data.token, authEmail: data.email });
      sendResponse({ error: false, ...data });
    }).catch((e) => sendResponse({ error: true, message: e.message || "Network error" }));
    return true;
  }

  if (msg.action === "signup") {
    apiFetch("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email: msg.email, password: msg.password }),
    }).then(async ({ status, data }) => {
      if (status !== 200 || !data.token) return sendResponse({ error: true, message: data.detail || "Signup failed" });
      await chrome.storage.local.set({ authToken: data.token, authEmail: data.email });
      sendResponse({ error: false, ...data });
    }).catch((e) => sendResponse({ error: true, message: e.message || "Network error" }));
    return true;
  }

  if (msg.action === "joinTeam") {
    apiFetch("/api/teams/join", {
      method: "POST",
      body: JSON.stringify({ email: msg.email, password: msg.password, invite_code: msg.inviteCode }),
    }).then(async ({ status, data }) => {
      if (status !== 200 || !data.token) return sendResponse({ error: true, message: data.detail || "Couldn't join team" });
      await chrome.storage.local.set({ authToken: data.token, authEmail: msg.email });
      sendResponse({ error: false, ...data });
    }).catch((e) => sendResponse({ error: true, message: e.message || "Network error" }));
    return true;
  }

  if (msg.action === "logout") {
    chrome.storage.local.remove(["authToken", "authEmail"], () => sendResponse({ ok: true }));
    return true;
  }

  if (msg.action === "getAuth") {
    chrome.storage.local.get(["authToken", "authEmail"], (d) =>
      sendResponse({ loggedIn: !!d.authToken, email: d.authEmail || null })
    );
    return true;
  }

  // ── Guest email (free tier, no account) ──
  if (msg.action === "getEmail") {
    chrome.storage.local.get(["userEmail"], (d) => sendResponse({ email: d.userEmail || null }));
    return true;
  }

  if (msg.action === "saveEmail") {
    chrome.storage.local.set({ userEmail: msg.email }, () => sendResponse({ ok: true }));
    return true;
  }
});
