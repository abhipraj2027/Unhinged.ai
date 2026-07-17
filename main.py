import os
import json
import time
import asyncio
import logging
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from itsdangerous import URLSafeSerializer
import re

import database as db
from llm import call_llm, parse_roast_json

# ── Setup ─────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("unhinged")

app = FastAPI(title="UnHinged API", version="2.3.0")

templates = Jinja2Templates(directory="templates")
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(req, exc):
    return JSONResponse(status_code=429, content={"error": "Too many requests. Slow down."})


APP_URL = os.getenv("APP_URL", "http://localhost:8000")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "unhinged2026")
SECRET_KEY = os.getenv("SECRET_KEY", "unhinged-secret-key-change-me")

signer = URLSafeSerializer(SECRET_KEY)

origins = [
    "https://mail.google.com",
    APP_URL,
]
# Also allow any chrome-extension origin
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"(https://mail\.google\.com|chrome-extension://.*|" + re.escape(APP_URL) + r")",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.on_event("startup")
def startup():
    db.init_db()
    log.info("Database initialized")
    # Check env vars
    keys = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"]
    for k in keys:
        status = "✓" if os.getenv(k) else "✗ MISSING"
        log.info(f"  {k}: {status}")


# ── Models ────────────────────────────────────────────────────


class AnalyzeReq(BaseModel):
    email: str
    message: str


class SubscriptionReq(BaseModel):
    email: str


class ConfigReq(BaseModel):
    config_key: str
    config_value: str


class TestReq(BaseModel):
    message: str


class VerifyPaymentReq(BaseModel):
    email: str
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str


# ── Landing page ──────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ── Health ────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.3.0"}


# ── Analyze ───────────────────────────────────────────────────


def _email_key(request: Request):
    """Rate limit key by email in body."""
    return get_remote_address(request)


@app.post("/api/analyze")
@limiter.limit("10/minute")
async def analyze(body: AnalyzeReq, request: Request):
    email = body.email.strip().lower()
    message = body.message.strip()

    if not email or not message:
        raise HTTPException(400, "Email and message are required")
    if len(message) > 2500:
        raise HTTPException(400, "Message too long (max 2000 chars)")

    user = db.get_or_create_user(email)
    remaining = db.scans_remaining(user)

    if remaining <= 0:
        return JSONResponse(
            status_code=402,
            content={
                "error": "trial_ended",
                "message": "Free trial ended. Upgrade to Pro for unlimited scans.",
                "scans_used": user["scans_used"],
                "limit": db.FREE_LIMIT,
            },
        )

    # Load LLM config
    cfg = db.get_all_config()
    roast_provider = cfg.get("roast_provider", "anthropic")
    roast_model = cfg.get("roast_model", "claude-haiku-4-5-20251001")
    roast_prompt = cfg.get("roast_prompt", "")
    roast_max = int(cfg.get("roast_max_tokens", "600"))
    roast_temp = float(cfg.get("roast_temperature", "1.0"))

    rewrite_provider = cfg.get("rewrite_provider", "openai")
    rewrite_model = cfg.get("rewrite_model", "gpt-4o-mini")
    rewrite_prompt = cfg.get("rewrite_prompt", "")
    rewrite_max = int(cfg.get("rewrite_max_tokens", "800"))
    rewrite_temp = float(cfg.get("rewrite_temperature", "0.7"))

    try:
        roast_raw, rewrite_raw = await asyncio.gather(
            call_llm(
                roast_provider,
                roast_model,
                roast_prompt,
                f"Analyze this email draft and roast it. Return ONLY valid JSON:\n\n---\n{message}\n---",
                roast_max,
                roast_temp,
            ),
            call_llm(
                rewrite_provider,
                rewrite_model,
                rewrite_prompt,
                f"Rewrite this email professionally. Return ONLY the rewritten email:\n\n---\n{message}\n---",
                rewrite_max,
                rewrite_temp,
            ),
        )
    except PermissionError as e:
        log.error(f"API key error: {e}")
        raise HTTPException(500, "API configuration error. Contact support.")
    except ConnectionError as e:
        raise HTTPException(429, str(e))
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        raise HTTPException(500, "Analysis failed. Please try again.")

    roast_data = parse_roast_json(roast_raw)
    db.increment_scan(email)
    user = db.get_or_create_user(email)  # refresh

    return {
        "score": roast_data.get("score", 5),
        "roast": roast_data.get("roast", ""),
        "risk": roast_data.get("risk", ""),
        "rewrite": rewrite_raw.strip(),
        "scans_used": user["scans_used"],
        "scans_remaining": db.scans_remaining(user),
        "is_pro": bool(user["is_pro"]),
    }


# ── Check status ──────────────────────────────────────────────


@app.get("/api/check-status")
async def check_status(email: str):
    email = email.strip().lower()
    user = db.get_user(email)
    if not user:
        return {
            "email": email,
            "is_pro": False,
            "scans_used": 0,
            "scans_remaining": db.FREE_LIMIT,
            "limit": db.FREE_LIMIT,
            "expires_at": None,
        }
    return {
        "email": email,
        "is_pro": bool(user["is_pro"]),
        "scans_used": user["scans_used"],
        "scans_remaining": db.scans_remaining(user),
        "limit": db.FREE_LIMIT,
        "expires_at": user.get("expires_at"),
    }


# ── Razorpay ──────────────────────────────────────────────────


def _get_razorpay():
    import razorpay

    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise HTTPException(500, "Payment system not configured")
    return razorpay.Client(auth=(key_id, key_secret))


@app.post("/api/create-subscription")
@limiter.limit("3/minute")
async def create_subscription(body: SubscriptionReq, request: Request):
    email = body.email.strip().lower()
    user = db.get_or_create_user(email)

    if user["is_pro"]:
        return {"already_pro": True}

    plan_id = os.getenv("RAZORPAY_PLAN_ID")
    if not plan_id:
        raise HTTPException(500, "Subscription plan not configured")

    try:
        client = _get_razorpay()
        sub = client.subscription.create(
            {
                "plan_id": plan_id,
                "total_count": 12,
                "quantity": 1,
                "notes": {"email": email, "product": "unhinged_pro"},
            }
        )
        db.get_or_create_user(email)  # ensure user exists
        with db.get_db() as conn:
            conn.execute(
                "UPDATE users SET razorpay_subscription_id=? WHERE email=?",
                (sub["id"], email),
            )
        return {"subscription_id": sub["id"], "payment_link": sub.get("short_url", "")}
    except Exception as e:
        log.error(f"Razorpay create-subscription error: {e}")
        raise HTTPException(500, f"Payment error: {str(e)}")


@app.post("/api/razorpay-webhook")
async def razorpay_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Razorpay-Signature", "")
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

    if secret:
        try:
            client = _get_razorpay()
            client.utility.verify_webhook_signature(body.decode(), sig, secret)
        except Exception:
            log.warning("Webhook signature verification failed")
            raise HTTPException(400, "Invalid signature")

    payload = json.loads(body)
    event = payload.get("event", "")
    log.info(f"Razorpay webhook: {event}")

    # Extract email from payload
    email = None
    try:
        entity = payload.get("payload", {})
        # Try subscription notes first
        sub_entity = entity.get("subscription", {}).get("entity", {})
        email = sub_entity.get("notes", {}).get("email")
        # Fallback to payment entity
        if not email:
            pay_entity = entity.get("payment", {}).get("entity", {})
            email = pay_entity.get("email")
            if not email:
                email = pay_entity.get("notes", {}).get("email")
    except Exception:
        pass

    if not email:
        log.warning(f"Webhook {event}: could not extract email")
        return {"status": "ok"}

    sub_id = None
    pay_id = None
    try:
        sub_id = payload["payload"]["subscription"]["entity"]["id"]
    except Exception:
        pass
    try:
        pay_id = payload["payload"]["payment"]["entity"]["id"]
    except Exception:
        pass

    if event in ("subscription.activated", "subscription.charged", "payment.captured"):
        db.set_pro(email, subscription_id=sub_id, payment_id=pay_id)
        log.info(f"Pro activated for {email}")
    elif event in ("subscription.cancelled", "subscription.paused", "subscription.completed"):
        db.unset_pro(email)
        log.info(f"Pro deactivated for {email}")

    return {"status": "ok"}


@app.post("/api/verify-payment")
async def verify_payment(body: VerifyPaymentReq):
    try:
        client = _get_razorpay()
        client.utility.verify_payment_signature(
            {
                "razorpay_payment_id": body.razorpay_payment_id,
                "razorpay_subscription_id": body.razorpay_subscription_id,
                "razorpay_signature": body.razorpay_signature,
            }
        )
    except Exception:
        raise HTTPException(400, "Payment verification failed")

    db.set_pro(body.email, subscription_id=body.razorpay_subscription_id, payment_id=body.razorpay_payment_id)
    return {"success": True, "is_pro": True}


# ── Admin ─────────────────────────────────────────────────────


def _check_admin(request: Request):
    token = request.cookies.get("admin_token")
    if not token:
        return False
    try:
        data = signer.loads(token)
        return data == "admin_authenticated"
    except Exception:
        return False


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request, "authenticated": _check_admin(request)})


@app.post("/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    pw = body.get("password", "")
    if pw != ADMIN_PASSWORD:
        raise HTTPException(401, "Wrong password")
    token = signer.dumps("admin_authenticated")
    resp = JSONResponse({"success": True})
    resp.set_cookie("admin_token", token, httponly=True, samesite="lax", max_age=86400)
    return resp


@app.get("/admin/config")
async def admin_get_config(request: Request):
    if not _check_admin(request):
        raise HTTPException(401, "Unauthorized")
    cfg = db.get_all_config()
    return cfg


@app.post("/admin/config")
async def admin_set_config(body: ConfigReq, request: Request):
    if not _check_admin(request):
        raise HTTPException(401, "Unauthorized")
    db.set_config(body.config_key, body.config_value)
    return {"success": True, "updated": body.config_key}


@app.post("/admin/test-analyze")
async def admin_test(body: TestReq, request: Request):
    if not _check_admin(request):
        raise HTTPException(401, "Unauthorized")

    cfg = db.get_all_config()
    t0 = time.time()
    try:
        roast_raw, rewrite_raw = await asyncio.gather(
            call_llm(
                cfg.get("roast_provider", "anthropic"),
                cfg.get("roast_model", "claude-haiku-4-5-20251001"),
                cfg.get("roast_prompt", ""),
                f"Analyze this email draft and roast it. Return ONLY valid JSON:\n\n---\n{body.message}\n---",
                int(cfg.get("roast_max_tokens", "600")),
                float(cfg.get("roast_temperature", "1.0")),
            ),
            call_llm(
                cfg.get("rewrite_provider", "openai"),
                cfg.get("rewrite_model", "gpt-4o-mini"),
                cfg.get("rewrite_prompt", ""),
                f"Rewrite this email professionally. Return ONLY the rewritten email:\n\n---\n{body.message}\n---",
                int(cfg.get("rewrite_max_tokens", "800")),
                float(cfg.get("rewrite_temperature", "0.7")),
            ),
        )
    except Exception as e:
        return {"error": str(e)}

    elapsed = int((time.time() - t0) * 1000)
    roast_data = parse_roast_json(roast_raw)
    return {
        **roast_data,
        "rewrite": rewrite_raw.strip(),
        "total_time_ms": elapsed,
        "roast_model": cfg.get("roast_model"),
        "rewrite_model": cfg.get("rewrite_model"),
    }


@app.get("/admin/stats")
async def admin_stats(request: Request):
    if not _check_admin(request):
        raise HTTPException(401, "Unauthorized")
    return db.get_stats()


@app.get("/admin/env-check")
async def admin_env(request: Request):
    if not _check_admin(request):
        raise HTTPException(401, "Unauthorized")
    keys = [
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
        "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "RAZORPAY_PLAN_ID",
    ]
    return {k: ("set" if os.getenv(k) else "missing") for k in keys}

# ── ENV VAR OVERRIDES for LLM config ──────────────────────
# If DB config fails, these env vars take precedence
import os as _os
_PROVIDER_OVERRIDES = {
    "roast_provider": _os.getenv("LLM_ROAST_PROVIDER"),
    "roast_model": _os.getenv("LLM_ROAST_MODEL"),
    "rewrite_provider": _os.getenv("LLM_REWRITE_PROVIDER"),
    "rewrite_model": _os.getenv("LLM_REWRITE_MODEL"),
}

_original_get_all_config = db.get_all_config
def _patched_get_all_config():
    cfg = _original_get_all_config()
    for k, v in _PROVIDER_OVERRIDES.items():
        if v:
            cfg[k] = v
    return cfg
db.get_all_config = _patched_get_all_config
