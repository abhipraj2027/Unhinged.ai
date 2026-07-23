import os, json, time, asyncio, re, logging
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

app = FastAPI(title="UnHinged API", version="2.5.0")
templates = Jinja2Templates(directory="templates")

APP_URL = os.getenv("APP_URL","http://localhost:8000")
ADMIN_PW = os.getenv("ADMIN_PASSWORD","unhinged2026")
signer = URLSafeSerializer(os.getenv("SECRET_KEY","unhinged-secret"))

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
class VerifyReq(BaseModel):
    email: str
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str

def _admin_ok(req):
    try: return signer.loads(req.cookies.get("admin_token","")) == "admin_ok"
    except: return False

# -- Pages --
@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/health")
async def health():
    cfg = db.get_config()
    return {"status":"ok","version":"2.5.0","provider":cfg.get("roast_provider","?"),"model":cfg.get("roast_model","?")}

# -- Analyze --
@app.post("/api/analyze")
async def analyze(body: AnalyzeReq):
    email = body.email.strip().lower()
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
    rp = cfg.get("roast_provider","groq")
    rm = cfg.get("roast_model","llama-3.3-70b-versatile")
    rprompt = cfg.get("roast_prompt","")
    rmax = int(cfg.get("roast_max_tokens","600"))
    rtemp = float(cfg.get("roast_temperature","1.0"))
    wp = cfg.get("rewrite_provider","groq")
    wm = cfg.get("rewrite_model","llama-3.3-70b-versatile")
    wprompt = cfg.get("rewrite_prompt","")
    wmax = int(cfg.get("rewrite_max_tokens","800"))
    wtemp = float(cfg.get("rewrite_temperature","0.7"))
    log.info(f"Analyze — email:{email}, len:{len(message)}, roast:{rp}/{rm}, rewrite:{wp}/{wm}")
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
    except Exception as e:
        log.error(f"LLM error: {e}")
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
    email = None
    try:
        email = payload["payload"]["subscription"]["entity"]["notes"].get("email")
        if not email: email = payload["payload"]["payment"]["entity"].get("email")
    except: pass
    if not email: return {"status":"ok"}
    sub_id = pay_id = None
    try: sub_id = payload["payload"]["subscription"]["entity"]["id"]
    except: pass
    try: pay_id = payload["payload"]["payment"]["entity"]["id"]
    except: pass
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
    db.set_pro(body.email, body.razorpay_subscription_id, body.razorpay_payment_id)
    return {"success":True,"is_pro":True}

# -- Auth --
class SignupReq(BaseModel):
    email: str
    password: str
    name: str = ""

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
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if db.has_password(email):
        raise HTTPException(400, "Account already exists. Login instead.")
    db.get_or_create(email)
    db.set_password(email, body.password)
    token = signer.dumps({"email": email, "type": "user"})
    resp = JSONResponse({"success": True, "email": email, "is_pro": bool(db.get_user(email).get("is_pro"))})
    resp.set_cookie("user_token", token, httponly=True, samesite="lax", max_age=30*86400)
    return resp

@app.post("/api/auth/login")
async def login(body: LoginReq):
    email = body.email.strip().lower()
    if not db.check_password(email, body.password):
        raise HTTPException(401, "Wrong email or password")
    user = db.get_user(email)
    user = db.reset_daily(email) or user
    team = db.get_team_for_email(email)
    token = signer.dumps({"email": email, "type": "user"})
    resp = JSONResponse({
        "success": True,
        "email": email,
        "is_pro": bool(user.get("is_pro")),
        "team": team.get("name") if team else None,
        "scans_remaining": db.remaining(user),
        "daily_limit": db.limit_for(user),
    })
    resp.set_cookie("user_token", token, httponly=True, samesite="lax", max_age=30*86400)
    return resp

@app.get("/api/auth/me")
async def auth_me(request: Request):
    try:
        data = signer.loads(request.cookies.get("user_token", ""))
        email = data.get("email", "")
    except:
        return JSONResponse(status_code=401, content={"error": "Not logged in"})
    user = db.get_user(email)
    if not user:
        return JSONResponse(status_code=401, content={"error": "User not found"})
    user = db.reset_daily(email) or user
    team = db.get_team_for_email(email)
    return {
        "email": email,
        "is_pro": bool(user.get("is_pro")),
        "team": team.get("name") if team else None,
        "team_id": team.get("id") if team else None,
        "scans_remaining": db.remaining(user),
        "daily_limit": db.limit_for(user),
    }

@app.post("/api/auth/logout")
async def logout():
    resp = JSONResponse({"success": True})
    resp.delete_cookie("user_token")
    return resp

@app.post("/api/teams/join")
async def join_team(body: JoinTeamReq):
    email = body.email.strip().lower()
    # Create account if needed
    if not db.has_password(email):
        if len(body.password) < 6:
            raise HTTPException(400, "Password must be at least 6 characters")
        db.get_or_create(email)
        db.set_password(email, body.password)
    else:
        if not db.check_password(email, body.password):
            raise HTTPException(401, "Wrong password for existing account")
    result = db.join_team_by_code(email, body.invite_code.strip().upper())
    if result.get("error"):
        raise HTTPException(400, result["error"])
    token = signer.dumps({"email": email, "type": "user"})
    resp = JSONResponse({"success": True, "team_name": result.get("team_name"), "is_pro": True})
    resp.set_cookie("user_token", token, httponly=True, samesite="lax", max_age=30*86400)
    return resp

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

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
    if not _admin_ok(request): raise HTTPException(401)
    result = db.add_team_member(body.team_id, body.email)
    return result

@app.post("/api/teams/remove-member")
async def remove_member(body: TeamMemberReq, request: Request):
    if not _admin_ok(request): raise HTTPException(401)
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
async def team_leaderboard(team_id: int, days: int = 7):
    return db.get_leaderboard(team_id, days)

@app.get("/api/teams/my-team")
async def my_team(email: str):
    email = email.strip().lower()
    team = db.get_team_for_email(email)
    if not team:
        return {"has_team": False}
    members = db.get_team_members(team["id"])
    leaderboard = db.get_leaderboard(team["id"])
    return {"has_team": True, "team": team, "members": members, "leaderboard": leaderboard}

@app.get("/teams", response_class=HTMLResponse)
async def teams_page(request: Request):
    return templates.TemplateResponse("teams.html", {"request": request})

# -- Admin --
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html",{"request":request})

@app.post("/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    if body.get("password") != ADMIN_PW: raise HTTPException(401,"Wrong password")
    resp = JSONResponse({"success":True})
    resp.set_cookie("admin_token", signer.dumps("admin_ok"), httponly=True, samesite="lax", max_age=86400)
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
    t0 = time.time()
    try:
        roast_raw, rewrite_raw = await asyncio.gather(
            call_llm(cfg.get("roast_provider","groq"),cfg.get("roast_model","llama-3.3-70b-versatile"),cfg.get("roast_prompt",""),f"Analyze and roast. Return ONLY JSON:\n\n---\n{body.message}\n---",int(cfg.get("roast_max_tokens","600")),float(cfg.get("roast_temperature","1.0"))),
            call_llm(cfg.get("rewrite_provider","groq"),cfg.get("rewrite_model","llama-3.3-70b-versatile"),cfg.get("rewrite_prompt",""),f"Rewrite professionally:\n\n---\n{body.message}\n---",int(cfg.get("rewrite_max_tokens","800")),float(cfg.get("rewrite_temperature","0.7"))),
        )
    except Exception as e:
        return {"error":str(e)}
    roast = parse_roast_json(roast_raw)
    return {**roast,"rewrite":rewrite_raw.strip(),"time_ms":int((time.time()-t0)*1000),"roast_model":cfg.get("roast_model"),"rewrite_model":cfg.get("rewrite_model")}

@app.get("/admin/stats")
async def admin_stats(request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    return db.get_stats()

@app.get("/admin/env-check")
async def admin_env(request: Request):
    if not _admin_ok(request): raise HTTPException(401)
    return {k:("set" if os.getenv(k) else "missing") for k in ["GROQ_API_KEY","ANTHROPIC_API_KEY","OPENAI_API_KEY","GOOGLE_API_KEY","RAZORPAY_KEY_ID","RAZORPAY_KEY_SECRET","RAZORPAY_WEBHOOK_SECRET","RAZORPAY_PLAN_ID"]}
