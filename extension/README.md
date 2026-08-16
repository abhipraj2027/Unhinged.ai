# UnHinged Chrome Extension (v3.0)

Manifest V3 extension for Gmail. Injects a floating panel into `mail.google.com`
that scores, roasts, and rewrites the open compose draft.

## Load unpacked (dev/testing)
1. `chrome://extensions`
2. Enable "Developer mode" (top right)
3. "Load unpacked" → select this `extension/` folder
4. Open Gmail, click the 🔥 icon bottom-right (or click the toolbar extension icon)

## Auth
- **Login / Sign Up / Join Team** — stores a JWT (`chrome.storage.local`, key
  `authToken`) and sends it as `Authorization: Bearer <token>` on
  `/api/auth/me` and `/api/analyze`. This is the same token issued by the web
  app's `/api/auth/signup`, `/login`, and `/api/teams/join` — those endpoints
  return `token` in the JSON body specifically so the extension can grab it
  (the web app itself just uses the httpOnly cookie, extensions can't).
- **Guest** — no account, just an email string tracked server-side for the
  free daily quota (`chrome.storage.local` key `userEmail`). No team access,
  no cross-device sync.

## Config
`background.js` has one constant to update if the backend URL ever changes:
```js
const BACKEND = "https://unhinged.email";
```

## Packaging for the Chrome Web Store
```bash
cd extension
zip -r ../unhinged-extension.zip . -x "*.DS_Store"
```

## Backend endpoints this extension depends on
- `POST /api/auth/login`, `/signup`, `/api/teams/join` — must return `token` in the body
- `GET /api/auth/me` — Bearer-token identity check (team-aware)
- `GET /api/check-status?email=` — unauthenticated guest-mode status
- `POST /api/analyze` — scan endpoint
- `POST /api/create-subscription` — Pro upgrade checkout
