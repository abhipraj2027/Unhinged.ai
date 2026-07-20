import os
import json
import re
import time
import httpx

ANTHROPIC_KEY = lambda: os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_KEY = lambda: os.getenv("OPENAI_API_KEY", "")
GOOGLE_KEY = lambda: os.getenv("GOOGLE_API_KEY", "")


async def call_llm(
    provider: str,
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 600,
    temperature: float = 1.0,
) -> str:
    """Call an LLM and return raw text response."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        if provider == "anthropic":
            return await _call_anthropic(client, model, system_prompt, user_message, max_tokens, temperature)
        elif provider == "openai":
            return await _call_openai(client, model, system_prompt, user_message, max_tokens, temperature)
        elif provider == "google":
            return await _call_google(client, model, system_prompt, user_message, max_tokens, temperature)
        else:
            raise ValueError(f"Unknown provider: {provider}")


async def _call_anthropic(client, model, system, user_msg, max_tokens, temp):
    r = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY(),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temp,
            "system": system,
            "messages": [{"role": "user", "content": user_msg}],
        },
    )
    if r.status_code == 401:
        raise PermissionError("Invalid Anthropic API key")
    if r.status_code == 429:
        raise ConnectionError("Anthropic rate limited — wait a moment")
    r.raise_for_status()
    return r.json()["content"][0]["text"]


async def _call_openai(client, model, system, user_msg, max_tokens, temp):
    r = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY()}",
            "Content-Type": "application/json",
        },
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
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def _call_google(client, model, system, user_msg, max_tokens, temp):
    key = GOOGLE_KEY()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    # Try all possible auth methods for Google API keys
    if key.startswith("AIzaSy"):
        url = url + f"?key={key}"
        headers = {"Content-Type": "application/json"}
    else:
        # AQ. and other newer key formats use x-goog-api-key header
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        }

    r = await client.post(
        url,
        headers=headers,
        json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user_msg}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temp,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
    )
    if r.status_code in (401, 403, 404, 400):
        import logging
        logging.getLogger("unhinged").error(f"Google API error {r.status_code}: {r.text[:500]}")
        if r.status_code == 404:
            raise ValueError(f"Model '{model}' not found. Check model name.")
        raise PermissionError(f"Google API error ({r.status_code}): {r.text[:200]}")
    r.raise_for_status()
    data = r.json()
    # Gemini can return multiple parts (thinking + response)
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    # Get the last text part (skip thinking parts)
    text = ""
    for p in parts:
        if "text" in p:
            text = p["text"]
    return text


def parse_roast_json(raw: str) -> dict:
    """Extract JSON from LLM response — handles markdown, thinking, extra text."""
    import logging
    log = logging.getLogger("unhinged")
    log.info(f"Raw roast response (first 500 chars): {raw[:500]}")

    cleaned = raw.strip()
    # Strip ALL markdown code fences (could be multiple)
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"```", "", cleaned)
    cleaned = cleaned.strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object anywhere in text
    # Use greedy match for the outermost braces
    matches = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned)
    for m in matches:
        try:
            data = json.loads(m)
            if "score" in data or "roast" in data:
                return data
        except json.JSONDecodeError:
            continue

    # Last resort: try to find any {...} block
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # Manual extraction as final fallback
    score = 5.0
    sm = re.search(r"[\"\'](score)[\"\'\s]*:\s*([\d.]+)", cleaned, re.IGNORECASE)
    if sm:
        try:
            score = float(sm.group(2))
        except ValueError:
            pass

    roast = cleaned[:300] if len(cleaned) > 10 else "Couldn't parse the roast. Try again."
    return {"score": score, "roast": roast, "risk": "See the roast above for risk details."}
