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
    r = await client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GOOGLE_KEY()}",
        headers={"Content-Type": "application/json"},
        json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user_msg}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temp},
        },
    )
    if r.status_code == 401 or r.status_code == 403:
        raise PermissionError("Invalid Google API key")
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def parse_roast_json(raw: str) -> dict:
    """Extract JSON from Claude's response, handling markdown wrapping."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            return json.loads(m.group(0))
        return {"score": 5, "roast": "Couldn't parse the roast. Try again.", "risk": "Unknown risk level."}
