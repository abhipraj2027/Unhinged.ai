import os
import json
import re
import logging
import httpx

log = logging.getLogger("unhinged")

ANTHROPIC_KEY = lambda: os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_KEY    = lambda: os.getenv("OPENAI_API_KEY", "")
GOOGLE_KEY    = lambda: os.getenv("GOOGLE_API_KEY", "")
GROQ_KEY      = lambda: os.getenv("GROQ_API_KEY", "")


async def call_llm(provider, model, system_prompt, user_message, max_tokens=600, temperature=1.0):
    async with httpx.AsyncClient(timeout=30.0) as client:
        if provider == "groq":
            return await _call_groq(client, model, system_prompt, user_message, max_tokens, temperature)
        elif provider == "anthropic":
            return await _call_anthropic(client, model, system_prompt, user_message, max_tokens, temperature)
        elif provider == "openai":
            return await _call_openai(client, model, system_prompt, user_message, max_tokens, temperature)
        elif provider == "google":
            return await _call_google(client, model, system_prompt, user_message, max_tokens, temperature)
        else:
            raise ValueError(f"Unknown provider: {provider}")


async def _call_groq(client, model, system, user_msg, max_tokens, temp):
    key = GROQ_KEY()
    if not key:
        raise PermissionError("GROQ_API_KEY not set")
    r = await client.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temp,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        },
    )
    if r.status_code == 401:
        raise PermissionError("Invalid Groq API key")
    if r.status_code == 429:
        raise ConnectionError("Groq rate limited — wait a moment")
    if r.status_code >= 400:
        log.error(f"Groq error {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def _call_anthropic(client, model, system, user_msg, max_tokens, temp):
    key = ANTHROPIC_KEY()
    if not key:
        raise PermissionError("ANTHROPIC_API_KEY not set")
    r = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temp,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_msg}],
        },
    )
    if r.status_code == 401:
        raise PermissionError("Invalid Anthropic API key")
    if r.status_code == 429:
        raise ConnectionError("Anthropic rate limited — wait a moment")
    if r.status_code >= 400:
        log.error(f"Anthropic error {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
    return r.json()["content"][0]["text"]


async def _call_openai(client, model, system, user_msg, max_tokens, temp):
    key = OPENAI_KEY()
    if not key:
        raise PermissionError("OPENAI_API_KEY not set")
    r = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temp,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        },
    )
    if r.status_code == 401:
        raise PermissionError("Invalid OpenAI API key")
    if r.status_code == 429:
        raise ConnectionError("OpenAI rate limited — wait a moment")
    if r.status_code >= 400:
        log.error(f"OpenAI error {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def _call_google(client, model, system, user_msg, max_tokens, temp):
    key = GOOGLE_KEY()
    if not key:
        raise PermissionError("GOOGLE_API_KEY not set")
    if key.startswith("AIzaSy"):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        headers = {"Content-Type": "application/json"}
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    r = await client.post(
        url, headers=headers,
        json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user_msg}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temp},
        },
    )
    if r.status_code >= 400:
        log.error(f"Google error {r.status_code}: {r.text[:300]}")
        if r.status_code == 404:
            raise ValueError(f"Model '{model}' not found")
        if r.status_code in (401, 403):
            raise PermissionError(f"Google API error ({r.status_code})")
        r.raise_for_status()
    data = r.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = ""
    for p in parts:
        if "text" in p:
            text = p["text"]
    return text


def parse_roast_json(raw):
    log.info(f"Roast response (first 300): {raw[:300]}")
    cleaned = re.sub(r"```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    matches = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned)
    for m in matches:
        try:
            d = json.loads(m)
            if "score" in d or "roast" in d:
                return d
        except json.JSONDecodeError:
            continue
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    score = 5.0
    sm = re.search(r"[\"']?score[\"']?\s*:\s*([\d.]+)", cleaned, re.IGNORECASE)
    if sm:
        try:
            score = float(sm.group(1))
        except ValueError:
            pass
    return {"score": score, "roast": cleaned[:300] if len(cleaned) > 10 else "Couldn't parse. Try again.", "risk": "Unknown."}
