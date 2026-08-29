import os, json, time, asyncio, re, logging
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from itsdangerous import URLSafeSerializer
import database as db
from llm import call_llm, parse_roast_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("unhinged")

app = FastAPI(title="UnHinged API", version="2.5.0", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory="templates")

APP_URL = os.getenv("APP_URL","http://localhost:8000")

# -- IP-based rate limit (defense-in-depth on top of per-email daily quota,
#    since the email on /api/analyze is self-reported and unverified) --
_ip_hits = {}
_IP_LIMIT = 15       # requests
_IP_WINDOW = 3600     # seconds

def _get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _ip_rate_limited(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _ip_hits.get(ip, []) if now - t < _IP_WINDOW]
    hits.append(now)
    _ip_hits[ip] = hits
    return len(hits) > _IP_LIMIT

ADMIN_PW = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PW:
    raise RuntimeError("ADMIN_PASSWORD env var not set — refusing to start with an insecure default")
_SECRET_KEY = os.getenv("SECRET_KEY")
if not _SECRET_KEY:
    raise RuntimeError("SECRET_KEY env var not set — refusing to start with an insecure default")
signer = URLSafeSerializer(_SECRET_KEY)

app.add_middleware(CORSMiddleware,
    allow_origin_regex=r"(https://mail\.google\.com|chrome-extension://.*|" + re.escape(APP_URL) + r")",
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    db.init_db()
    log.info("DB initialized")
    for k in ["GROQ_API_KEY","ANTHROPIC_API_KEY","OPENAI_API_KEY","GOOGLE_API_KEY","RAZORPAY_KEY_ID"]:
        log.info(f"  {k}: {'✓' if os.getenv(k) else '✗'}")

# -- Models --
class AnalyzeReq(BaseModel):
    email: str
    message: str
class SubReq(BaseModel):
    email: str
class CfgReq(BaseModel):
    config_key: str
    config_value: str
class TestReq(BaseModel):
    message: str
    tier: str = "pro"  # "free" or "pro"
class VerifyReq(BaseModel):
    email: str
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str
class TeamCheckoutReq(BaseModel):
    name: str
    seats: int = 5
    member_emails: list[str] = []
class RotateCodeReq(BaseModel):
    team_id: int

def _admin_ok(req):
    try: return signer.loads(req.cookies.get("admin_token","")) == "admin_ok"
    except: return False

# -- Pages --
@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/robots.txt", response_class=HTMLResponse)
async def robots():
    return HTMLResponse(
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /account\n"
        "Disallow: /teams\n"
        "Disallow: /api/\n"
        f"Sitemap: {APP_URL}/sitemap.xml\n",
        media_type="text/plain",
    )

@app.get("/sitemap.xml")
async def sitemap():
    pages = ["/", "/login", "/privacy", "/terms"]
    urls = "".join(f"<url><loc>{APP_URL}{p}</loc></url>" for p in pages)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return HTMLResponse(xml, media_type="application/xml")

@app.get("/api/health")
async def health():
    cfg = db.get_config()
    return {"status":"ok","version":"2.6.0",
            "free_provider":cfg.get("roast_provider_free","?"),"free_model":cfg.get("roast_model_free","?"),
            "pro_provider":cfg.get("roast_provider_pro","?"),"pro_model":cfg.get("roast_model_pro","?")}

# -- Analyze --
@app.post("/api/analyze")
async def analyze(body: AnalyzeReq, request: Request):
    ip = _get_client_ip(request)
    if _ip_rate_limited(ip):
        raise HTTPException(429, "Too many requests from this network. Try again later.")
    # SECURITY: if the request carries a valid logged-in session, trust that
    # identity over whatever email is in the request body — otherwise anyone
    # could type a real Pro user's email here and ride their tier/quota
    # without ever proving they own that address. Anonymous/no-login usage
    # (the free email-capture flow on the landing page) is unaffected: with
    # no session cookie, we fall back to the client-supplied email as before.
    session_email = _get_current_user(request)
    email = session_email if session_email else body.email.strip().lower()
    message = body.message.strip()
    if not email or not message:
        raise HTTPException(400, "Email and message required")
    if len(message) < 10:
        raise HTTPException(400, "Message too short (min 10 chars)")
    if len(message) > 2500:
        raise HTTPException(400, "Message too long (max 2000 chars)")
    user = db.get_or_create(email)
    user = db.reset_daily(email) or user
    rem = db.remaining(user)
    lim = db.limit_for(user)
    if rem <= 0:
        msg = f"Daily limit reached ({lim}/day). Resets midnight UTC." if user.get("is_pro") else f"Free daily limit reached ({lim}/day). Upgrade to Pro for 30/day."
        return JSONResponse(status_code=402, content={"error":"limit_reached","message":msg,"is_pro":bool(user.get("is_pro")),"limit":lim})
    cfg = db.get_config()
    tier = "pro" if user.get("is_pro") else "free"
    rp = cfg.get(f"roast_provider_{tier}","groq")
    rm = cfg.get(f"roast_model_{tier}","openai/gpt-oss-120b")
    rprompt = cfg.get("roast_prompt","")
    rmax = int(cfg.get("roast_max_tokens","600"))
    rtemp = float(cfg.get("roast_temperature","1.0"))
    wp = cfg.get(f"rewrite_provider_{tier}","groq")
    wm = cfg.get(f"rewrite_model_{tier}","openai/gpt-oss-120b")
    wprompt = cfg.get("rewrite_prompt","")
    wmax = int(cfg.get("rewrite_max_tokens","800"))
    wtemp = float(cfg.get("rewrite_temperature","0.7"))
    log.info(f"Analyze — email:{email}, tier:{tier}, len:{len(message)}, roast:{rp}/{rm}, rewrite:{wp}/{wm}")
    try:
        roast_raw, rewrite_raw = await asyncio.gather(
            call_llm(rp, rm, rprompt, f"Analyze this email and roast it. Return ONLY valid JSON:\n\n---\n{message}\n---", rmax, rtemp),
            call_llm(wp, wm, wprompt, f"Rewrite this email professionally. Return ONLY the rewritten email:\n\n---\n{message}\n---", wmax, wtemp),
        )
    except PermissionError as e:
        log.error(f"API key error: {e}")
        raise HTTPException(500, f"API error: {e}")
    except ValueError as e:
        log.error(f"Model error: {e}")
        raise HTTPException(500, f"Model error: {e}")
    except ConnectionError as e:
        raise HTTPException(429, str(e))
    except httpx.TimeoutException as e:
        log.error(f"LLM timeout: {e}")
        raise HTTPException(504, "The AI took too long to respond. Please try again.")
    except httpx.HTTPStatusError as e:
        log.error(f"LLM HTTP error: {e.response.status_code} — {e.response.text[:300]}")
        raise HTTPException(502, "The AI provider returned an error. Please try again.")
    except httpx.RequestError as e:
        log.error(f"LLM network error: {e}")
        raise HTTPException(502, "Couldn't reach the AI provider. Please try again.")
    except Exception as e:
        log.error(f"LLM error: {type(e).__name__}: {e}")
        raise HTTPException(500, "Analysis failed. Please try again.")
    roast = parse_roast_json(roast_raw)
    db.inc_scan(email)
    db.log_scan(email, roast.get("score", 5))
    user = db.get_or_create(email)
    user = db.reset_daily(email) or user
    return {"score":roast.get("score",5),"roast":roast.get("roast",""),"risk":roast.get("risk",""),"rewrite":rewrite_raw.strip(),"scans_used":user.get("scans_used",0),"daily_scans":user.get("daily_scans",0),"scans_remaining":db.remaining(user),"daily_limit":db.limit_for(user),"is_pro":bool(user.get("is_pro"))}

# -- Status --
@app.get("/api/check-status")
async def check_status(email: str):
    email = email.strip().lower()
    user = db.get_user(email)
    if not user:
        return {"email":email,"is_pro":False,"scans_used":0,"daily_scans":0,"scans_remaining":db.FREE_DAILY,"daily_limit":db.FREE_DAILY,"limit":db.FREE_DAILY}
    user = db.reset_daily(email) or user
    team = db.get_team_for_email(email)
    if team and not user.get("is_pro"):
        db.get_or_create(email)
        with db.get_db() as conn:
            conn.execute("UPDATE users SET is_pro=1 WHERE email=?", (email,))
        user["is_pro"] = 1
    return {"email":email,"is_pro":bool(user.get("is_pro")),"team":team.get("name") if team else None,"scans_used":user.get("scans_used",0),"daily_scans":user.get("daily_scans",0),"scans_remaining":db.remaining(user),"daily_limit":db.limit_for(user),"limit":db.limit_for(user),"expires_at":user.get("expires_at")}

# -- Razorpay --
def _rz():
    import razorpay
    k,s = os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")
    if not k or not s: raise HTTPException(500,"Payment not configured")
    return razorpay.Client(auth=(k,s))

@app.post("/api/create-subscription")
async def create_sub(body: SubReq):
    email = body.email.strip().lower()
    user = db.get_or_create(email)
    if user.get("is_pro"): return {"already_pro":True}
    plan = os.getenv("RAZORPAY_PLAN_ID")
    if not plan: raise HTTPException(500,"Plan not configured")
    try:
        c = _rz()
        sub = c.subscription.create({"plan_id":plan,"total_count":12,"quantity":1,"notes":{"email":email,"product":"unhinged_pro"}})
        with db.get_db() as conn:
            conn.execute("UPDATE users SET razorpay_sub_id=? WHERE email=?",(sub["id"],email))
        return {"subscription_id":sub["id"],"payment_link":sub.get("short_url","")}
    except Exception as e:
        log.error(f"Razorpay error: {e}")
        raise HTTPException(500,f"Payment error: {e}")

@app.post("/api/teams/checkout")
async def team_checkout(body: TeamCheckoutReq, request: Request):
    email = _get_current_user(request)
    if not email:
        raise HTTPException(401, "Please log in first")
    if db.get_team_for_email(email):
        raise HTTPException(400, "You're already on a team")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Team name required")
    if len(name) > 60:
        raise HTTPException(400, "Team name too long (max 60 characters)")
    seats = max(2, min(int(body.seats), 200))
    member_emails = [e.strip().lower() for e in (body.member_emails or []) if e and e.strip()]
    if len(member_emails) > seats - 1:
        raise HTTPException(400, f"You've listed {len(member_emails)} members, but only {seats - 1} member seats are available (1 seat is you, the owner)")
    for e in member_emails:
        if "@" not in e or "." not in e.split("@")[-1]:
            raise HTTPException(400, f"'{e}' doesn't look like a valid email")
    plan = os.getenv("RAZORPAY_TEAM_PLAN_ID")
    if not plan: raise HTTPException(500, "Team plan not configured")
    try:
        c = _rz()
        sub = c.subscription.create({
            "plan_id": plan, "total_count": 12, "quantity": seats,
            "notes": {"type": "team", "owner_email": email, "team_name": name, "seats": str(seats)}
        })
        if member_emails:
            db.stash_pending_team_members(sub["id"], member_emails)
        return {"subscription_id": sub["id"], "payment_link": sub.get("short_url", "")}
    except Exception as e:
        log.error(f"Razorpay team checkout error: {e}")
        raise HTTPException(500, f"Payment error: {e}")

@app.post("/api/teams/rotate-code")
async def rotate_code(body: RotateCodeReq, request: Request):
    if not _team_owner_ok(request, body.team_id): raise HTTPException(401)
    new_code = db.rotate_invite_code(body.team_id)
    return {"success": True, "invite_code": new_code}

@app.post("/api/razorpay-webhook")
async def rz_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Razorpay-Signature","")
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET","")
    if secret:
        try:
            _rz().utility.verify_webhook_signature(body.decode(), sig, secret)
        except:
            raise HTTPException(400,"Invalid signature")
    payload = json.loads(body)
    event = payload.get("event","")
    log.info(f"Webhook: {event}")

    notes = {}
    try: notes = payload["payload"]["subscription"]["entity"].get("notes") or {}
    except: pass
    sub_id = pay_id = None
    try: sub_id = payload["payload"]["subscription"]["entity"]["id"]
    except: pass
    try: pay_id = payload["payload"]["payment"]["entity"]["id"]
    except: pass

    # -- Team subscriptions (per-seat) --
    if notes.get("type") == "team":
        owner_email = notes.get("owner_email")
        team_name = notes.get("team_name", "My Team")
        try: seats = int(notes.get("seats", 5))
        except: seats = 5
        if owner_email and sub_id:
            if event in ("subscription.activated", "subscription.charged"):
                team = db.create_team_from_subscription(team_name, owner_email, seats, sub_id)
                log.info(f"Team ON: {team_name} ({owner_email}), sub {sub_id}")
                added = db.apply_pending_team_members(team["id"], sub_id)
                for member_email in added:
                    try:
                        await _send_team_member_added_email(member_email, team_name)
                    except Exception as e:
                        log.warning(f"Could not send team-added email to {member_email}: {e}")
                if added:
                    log.info(f"Team {team_name}: auto-added {len(added)} members from checkout — {added}")
            elif event in ("subscription.cancelled", "subscription.completed"):
                db.deactivate_team_by_sub(sub_id)
                log.info(f"Team OFF: sub {sub_id}")
        return {"status": "ok"}

    # -- Individual Pro subscriptions --
    email = notes.get("email")
    if not email:
        try: email = payload["payload"]["payment"]["entity"].get("email")
        except: pass
    if not email: return {"status":"ok"}
    if event in ("subscription.activated","subscription.charged","payment.captured"):
        db.set_pro(email, sub_id, pay_id)
        log.info(f"Pro ON: {email}")
    elif event in ("subscription.cancelled","subscription.paused","subscription.completed"):
        db.unset_pro(email)
        log.info(f"Pro OFF: {email}")
    return {"status":"ok"}

@app.post("/api/verify-payment")
async def verify_pay(body: VerifyReq):
    try:
        _rz().utility.verify_payment_signature({"razorpay_payment_id":body.razorpay_payment_id,"razorpay_subscription_id":body.razorpay_subscription_id,"razorpay_signature":body.razorpay_signature})
    except: raise HTTPException(400,"Verification failed")
    # SECURITY: never trust the client-supplied email for who gets Pro.
    # A valid signature only proves this payment_id/subscription_id pair is real —
    # it says nothing about which email the request claims to be. Look up the
    # email our own server recorded when this subscription was created in
    # /api/create-subscription instead, so a genuine payment can't be replayed
    # with a different email to grant free Pro to arbitrary accounts.
    with db.get_db() as conn:
        row = conn.execute("SELECT email FROM users WHERE razorpay_sub_id=?", (body.razorpay_subscription_id,)).fetchone()
    if not row:
        log.warning(f"verify-payment: unknown subscription_id {body.razorpay_subscription_id} (claimed email: {body.email})")
        raise HTTPException(400, "Subscription not found. Please start checkout again.")
    real_email = row["email"]
    if real_email != body.email.strip().lower():
        log.warning(f"verify-payment: email mismatch on sub {body.razorpay_subscription_id} — claimed {body.email}, actual {real_email}")
    db.set_pro(real_email, body.razorpay_subscription_id, body.razorpay_payment_id)
    return {"success":True,"is_pro":True}

# -- Auth (JWT + bcrypt) --
import jwt as pyjwt
JWT_SECRET = _SECRET_KEY
JWT_ALGO = "HS256"
JWT_EXP = 30 * 86400  # 30 days

def _make_token(email):
    import time as _t
    return pyjwt.encode({"email": email, "exp": _t.time() + JWT_EXP, "iat": _t.time()}, JWT_SECRET, algorithm=JWT_ALGO)

def _get_current_user(request: Request):
    token = request.cookies.get("auth_token") or ""
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return None
    try:
        data = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return data.get("email")
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None

def _team_owner_ok(request: Request, team_id: int) -> bool:
    """True if the caller is the site admin, OR is logged in and owns this specific team."""
    if _admin_ok(request):
        return True
    email = _get_current_user(request)
    if not email:
        return False
    return db.get_member_role(team_id, email) == "owner"

def _team_member_ok(request: Request, team_id: int) -> bool:
    """True if the caller is the site admin, OR is logged in and belongs to this team (any role)."""
    if _admin_ok(request):
        return True
    email = _get_current_user(request)
    if not email:
        return False
    return db.get_member_role(team_id, email) is not None

class SignupReq(BaseModel):
    email: str
    password: str

class LoginReq(BaseModel):
    email: str
    password: str

class JoinTeamReq(BaseModel):
    email: str
    password: str
    invite_code: str

@app.post("/api/auth/signup")
async def signup(body: SignupReq):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if db.has_password(email):
        raise HTTPException(409, "Account already exists. Please login.")
    db.get_or_create(email)
    db.set_password(email, body.password)
    token = _make_token(email)
    profile = db.get_user_profile(email)
    resp = JSONResponse({"success": True, "token": token, **profile})
    resp.set_cookie("auth_token", token, httponly=True, samesite="lax", secure=True, max_age=JWT_EXP)
    return resp

@app.post("/api/auth/login")
async def login(body: LoginReq):
    email = body.email.strip().lower()
    if not db.check_password(email, body.password):
        raise HTTPException(401, "Invalid email or password")
    token = _make_token(email)
    profile = db.get_user_profile(email)
    resp = JSONResponse({"success": True, "token": token, **profile})
    resp.set_cookie("auth_token", token, httponly=True, samesite="lax", secure=True, max_age=JWT_EXP)
    return resp

@app.get("/api/auth/me")
async def auth_me(request: Request):
    email = _get_current_user(request)
    if not email:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    profile = db.get_user_profile(email)
    if not profile:
        return JSONResponse(status_code=401, content={"error": "Account not found"})
    return profile

@app.post("/api/auth/logout")
async def logout():
    resp = JSONResponse({"success": True})
    resp.delete_cookie("auth_token")
    return resp

@app.post("/api/auth/change-password")
async def change_password(request: Request):
    email = _get_current_user(request)
    if not email:
        raise HTTPException(401, "Not authenticated")
    body = await request.json()
    old_pw = body.get("old_password", "")
    new_pw = body.get("new_password", "")
    if not db.check_password(email, old_pw):
        raise HTTPException(400, "Current password is wrong")
    if len(new_pw) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    db.set_password(email, new_pw)
    return {"success": True}

@app.post("/api/auth/cancel-subscription")
async def cancel_sub(request: Request):
    email = _get_current_user(request)
    if not email: raise HTTPException(401, "Not authenticated")
    user = db.get_user(email)
    if not user or not user.get("is_pro"):
        raise HTTPException(400, "No active subscription")
    # If Razorpay subscription exists, cancel it
    sub_id = user.get("razorpay_sub_id")
    if sub_id:
        try:
            _rz().subscription.cancel(sub_id)
        except Exception as e:
            log.error(f"Razorpay cancel error: {e}")
    db.unset_pro(email)
    return {"success": True, "message": "Subscription cancelled"}

@app.post("/api/auth/delete-account")
async def delete_account(request: Request):
    email = _get_current_user(request)
    if not email: raise HTTPException(401, "Not authenticated")
    team = db.get_team_for_email(email)
    if team and team.get("owner_email") == email:
        raise HTTPException(400, "You own a team — delete or transfer it before deleting your account")
    user = db.get_user(email)
    if user and user.get("razorpay_sub_id"):
        try: _rz().subscription.cancel(user["razorpay_sub_id"])
        except: pass
    # Remove from teams
    with db.get_db() as conn:
        conn.execute("DELETE FROM team_members WHERE email=?", (email,))
        conn.execute("DELETE FROM scan_log WHERE email=?", (email,))
        conn.execute("DELETE FROM users WHERE email=?", (email,))
    resp = JSONResponse({"success": True})
    resp.delete_cookie("auth_token")
    return resp

class ForgotReq(BaseModel):
    email: str

class ResetReq(BaseModel):
    token: str
    new_password: str

async def _send_team_member_added_email(to_email, team_name):
    """Notify someone the founder added directly that they now have Pro access."""
    resend_key = os.getenv("RESEND_API_KEY")
    if not resend_key:
        return False
    login_url = f"{APP_URL}/login"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post("https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                json={
                    "from": os.getenv("FROM_EMAIL", "UnHinged <noreply@unhinged.email>"),
                    "to": [to_email],
                    "subject": f"You've been added to {team_name} on UnHinged — Pro unlocked 🔥",
                    "html": f'<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:20px"><h2 style="color:#FF5C00">🔥 UnHinged</h2><p><strong>{team_name}</strong> added you to their team, and you now have Pro access — 30 scans/day, priority analysis, on the web and in the Gmail extension.</p><p><a href="{login_url}" style="display:inline-block;padding:12px 24px;background:#FF5C00;color:#000;text-decoration:none;border-radius:8px;font-weight:bold">Log in with this email to get started</a></p><p style="color:#999;font-size:12px">Use this email address ({to_email}) when you sign up or log in to activate your Pro access.</p></div>'
                })
            return r.status_code == 200
    except Exception as e:
        log.error(f"Team-added email error: {e}")
        return False


async def forgot_password(body: ForgotReq):
    email = body.email.strip().lower()
    if not db.get_user(email):
        # Don't reveal if email exists
        return {"success": True, "message": "If this email exists, a reset link has been sent."}
    if not db.has_password(email):
        return {"success": True, "message": "If this email exists, a reset link has been sent."}
    token = db.create_reset_token(email)
    if not token:
        return {"success": True, "message": "If this email exists, a reset link has been sent."}
    reset_url = f"{APP_URL}/reset-password?token={token}"
    sent = await _send_reset_email(email, reset_url)
    if not sent:
        log.warning(f"Could not send reset email to {email} — RESEND_API_KEY missing or send failed")
    return {"success": True, "message": "If this email exists, a reset link has been sent."}

async def _send_reset_email(to_email, reset_url):
    """Send reset email via Resend or SMTP."""
    resend_key = os.getenv("RESEND_API_KEY")
    if resend_key:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post("https://api.resend.com/emails", 
                    headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                    json={
                        "from": os.getenv("FROM_EMAIL", "UnHinged <noreply@unhinged.email>"),
                        "to": [to_email],
                        "subject": "Reset your UnHinged password",
                        "html": f'<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:20px"><h2 style="color:#FF5C00">🔥 UnHinged</h2><p>Someone requested a password reset for your account.</p><p><a href="{reset_url}" style="display:inline-block;padding:12px 24px;background:#FF5C00;color:#000;text-decoration:none;border-radius:8px;font-weight:bold">Reset Password</a></p><p style="color:#999;font-size:12px">This link expires in 1 hour. If you did not request this, ignore this email.</p></div>'
                    })
                return r.status_code == 200
        except Exception as e:
            log.error(f"Resend error: {e}")
            return False
    # No email service configured — log the URL
    log.info(f"RESET LINK (no email service): {reset_url}")
    return False

@app.post("/api/auth/reset-password")
async def reset_password(body: ResetReq):
    if len(body.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    email = db.verify_reset_token(body.token)
    if not email:
        raise HTTPException(400, "Invalid or expired reset link. Request a new one.")
    db.set_password(email, body.new_password)
    db.use_reset_token(body.token)
    log.info(f"Password reset for {email}")
    return {"success": True, "message": "Password reset! You can now login."}

@app.get("/reset-password", response_class=HTMLResponse)
async def reset_page(request: Request):
    return templates.TemplateResponse("reset-password.html", {"request": request})

@app.post("/api/teams/join")
async def join_team(body: JoinTeamReq):
    email = body.email.strip().lower()
    if not db.has_password(email):
        if len(body.password) < 6:
            raise HTTPException(400, "Password must be at least 6 characters")
        db.get_or_create(email)
        db.set_password(email, body.password)
    else:
        if not db.check_password(email, body.password):
            raise HTTPException(401, "Wrong password for this account")
    result = db.join_team_by_code(email, body.invite_code.strip().upper())
    if result.get("error"):
        raise HTTPException(400, result["error"])
    token = _make_token(email)
    resp = JSONResponse({"success": True, "token": token, "team_name": result.get("team_name"), "is_pro": True})
    resp.set_cookie("auth_token", token, httponly=True, samesite="lax", secure=True, max_age=JWT_EXP)
    return resp

# Extension auth — returns token for chrome.storage
@app.post("/api/auth/extension-login")
async def ext_login(body: LoginReq):
    email = body.email.strip().lower()
    if not db.check_password(email, body.password):
        raise HTTPException(401, "Invalid email or password")
    token = _make_token(email)
    profile = db.get_user_profile(email)
    return {"success": True, "token": token, **profile}

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    return templates.TemplateResponse("account.html", {"request": request})

# -- Teams --
class TeamCreateReq(BaseModel):
    name: str
    owner_email: str
    seats: int = 5

class TeamMemberReq(BaseModel):
    team_id: int
    email: str

@app.post("/api/teams/create")
async def create_team(body: TeamCreateReq, request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    team = db.create_team(body.name, body.owner_email, body.seats)
    return {"success": True, "team": team}

@app.post("/api/teams/add-member")
async def add_member(body: TeamMemberReq, request: Request):
    if not _team_owner_ok(request, body.team_id): raise HTTPException(401)
    result = db.add_team_member(body.team_id, body.email)
    return result

@app.post("/api/teams/remove-member")
async def remove_member(body: TeamMemberReq, request: Request):
    if not _team_owner_ok(request, body.team_id): raise HTTPException(401)
    return db.remove_team_member(body.team_id, body.email)

@app.get("/api/teams/list")
async def list_teams(request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    return db.get_all_teams()

@app.get("/api/teams/{team_id}/members")
async def team_members(team_id: int, request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    return db.get_team_members(team_id)

@app.get("/api/teams/{team_id}/leaderboard")
async def team_leaderboard(team_id: int, request: Request, days: int = 7):
    if not _team_member_ok(request, team_id): raise HTTPException(401)
    return db.get_leaderboard(team_id, days)

@app.get("/api/teams/{team_id}/awards")
async def team_awards(team_id: int, request: Request):
    if not _team_member_ok(request, team_id): raise HTTPException(401)
    return db.get_team_awards(team_id)

@app.get("/api/teams/my-team")
async def my_team(request: Request):
    email = _get_current_user(request)
    if not email:
        raise HTTPException(401, "Login required")
    team = db.get_team_for_email(email)
    if not team:
        return {"has_team": False, "your_email": email}
    is_owner = team.get("owner_email") == email
    if not is_owner:
        team = {k: v for k, v in team.items() if k != "invite_code"}
    members = db.get_team_members(team["id"])
    leaderboard = db.get_leaderboard(team["id"])
    awards = db.get_team_awards(team["id"])
    return {"has_team": True, "your_email": email, "team": team, "members": members, "leaderboard": leaderboard, "awards": awards}

@app.get("/teams", response_class=HTMLResponse)
async def teams_page(request: Request):
    return templates.TemplateResponse("teams.html", {"request": request})

# -- Admin --
@app.get("/api/share-image")
async def share_image(score: float = 5.0, roast: str = "", risk: str = ""):
    from card_generator import generate_roast_card
    from fastapi.responses import Response
    png_bytes = generate_roast_card(score, roast, risk)
    return Response(content=png_bytes, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})

@app.get("/share", response_class=HTMLResponse)
async def share_page(request: Request):
    return templates.TemplateResponse("share.html", {"request": request})

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})

@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})

# Admin manual Pro management
@app.post("/admin/set-pro")
async def admin_set_pro(request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    body = await request.json()
    email = body.get("email","").strip().lower()
    action = body.get("action","grant")
    if not email: raise HTTPException(400,"Email required")
    if action == "grant":
        db.set_pro(email)
        return {"success":True,"message":f"Pro granted to {email}"}
    else:
        db.unset_pro(email)
        return {"success":True,"message":f"Pro revoked from {email}"}

@app.get("/admin/search-user")
async def admin_search(request: Request, email: str = ""):
    if not _admin_ok(request): raise HTTPException(401)
    email = email.strip().lower()
    if not email: return []
    user = db.get_user(email)
    if not user: return []
    user = db.reset_daily(email) or user
    user = {k: v for k, v in user.items() if k != "password_hash"}
    team = db.get_team_for_email(email)
    return {**user, "team": dict(team) if team else None, "scans_remaining": db.remaining(user)}

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html",{"request":request})

@app.post("/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    if body.get("password") != ADMIN_PW: raise HTTPException(401,"Wrong password")
    resp = JSONResponse({"success":True})
    resp.set_cookie("admin_token", signer.dumps("admin_ok"), httponly=True, samesite="lax", secure=True, max_age=86400)
    return resp

@app.get("/admin/config")
async def admin_cfg(request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    return db.get_config()

@app.post("/admin/config")
async def admin_set_cfg(body: CfgReq, request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    db.set_config(body.config_key, body.config_value)
    return {"success":True}

@app.post("/admin/test-analyze")
async def admin_test(body: TestReq, request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    cfg = db.get_config()
    tier = body.tier if body.tier in ("free","pro") else "pro"
    t0 = time.time()
    try:
        roast_raw, rewrite_raw = await asyncio.gather(
            call_llm(cfg.get(f"roast_provider_{tier}","groq"),cfg.get(f"roast_model_{tier}","openai/gpt-oss-120b"),cfg.get("roast_prompt",""),f"Analyze and roast. Return ONLY JSON:\n\n---\n{body.message}\n---",int(cfg.get("roast_max_tokens","600")),float(cfg.get("roast_temperature","1.0"))),
            call_llm(cfg.get(f"rewrite_provider_{tier}","groq"),cfg.get(f"rewrite_model_{tier}","openai/gpt-oss-120b"),cfg.get("rewrite_prompt",""),f"Rewrite professionally:\n\n---\n{body.message}\n---",int(cfg.get("rewrite_max_tokens","800")),float(cfg.get("rewrite_temperature","0.7"))),
        )
    except Exception as e:
        return {"error":str(e)}
    roast = parse_roast_json(roast_raw)
    return {**roast,"rewrite":rewrite_raw.strip(),"time_ms":int((time.time()-t0)*1000),"tier":tier,"roast_model":cfg.get(f"roast_model_{tier}"),"rewrite_model":cfg.get(f"rewrite_model_{tier}")}

@app.get("/admin/stats")
async def admin_stats(request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    return db.get_stats()

@app.get("/admin/env-check")
async def admin_env(request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    return {k:("set" if os.getenv(k) else "missing") for k in ["GROQ_API_KEY","ANTHROPIC_API_KEY","OPENAI_API_KEY","GOOGLE_API_KEY","RAZORPAY_KEY_ID","RAZORPAY_KEY_SECRET","RAZORPAY_WEBHOOK_SECRET","RAZORPAY_PLAN_ID","RAZORPAY_TEAM_PLAN_ID"]}
