"""Phase 6a - one function to talk to any LLM provider.

    ask_llm(system, user, temperature=0) -> str

Order: the provider in .env (anthropic / openai / gemini) if its key is set,
then local Ollama (http://localhost:11434). If both fail, LLMUnavailableError
is raised with a readable message (the UI shows it politely).
Plain HTTP calls via `requests`, so no provider SDK is needed.
"""
import requests

from src import config

LAST_PROVIDER = None  # which provider answered the most recent call (shown in the UI)


class LLMUnavailableError(RuntimeError):
    """No configured provider and no local Ollama could answer."""


def _anthropic(system, user, temperature, max_tokens):
    body = {"model": config.ANTHROPIC_MODEL, "max_tokens": max_tokens, "temperature": temperature,
            "system": system, "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    r = requests.post("https://api.anthropic.com/v1/messages", json=body, headers=headers,
                      timeout=config.LLM_TIMEOUT)
    if r.status_code == 400 and "temperature" in r.text:  # some models fix temperature
        body.pop("temperature")
        r = requests.post("https://api.anthropic.com/v1/messages", json=body, headers=headers,
                          timeout=config.LLM_TIMEOUT)
    r.raise_for_status()
    return "".join(block.get("text", "") for block in r.json()["content"] if block["type"] == "text")


def _openai(system, user, temperature, max_tokens):
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                      json={"model": config.OPENAI_MODEL, "temperature": temperature,
                            "max_tokens": max_tokens,
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}]},
                      timeout=config.LLM_TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _gemini(system, user, temperature, max_tokens):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    r = requests.post(url, headers={"x-goog-api-key": config.GEMINI_API_KEY},
                      json={"systemInstruction": {"parts": [{"text": system}]},
                            "contents": [{"role": "user", "parts": [{"text": user}]}],
                            "generationConfig": {"temperature": temperature,
                                                 "maxOutputTokens": max_tokens}},
                      timeout=config.LLM_TIMEOUT)
    r.raise_for_status()
    return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])


def _ollama(system, user, temperature, max_tokens):
    r = requests.post(f"{config.OLLAMA_URL}/api/chat",
                      json={"model": config.OLLAMA_MODEL, "stream": False,
                            "options": {"temperature": temperature, "num_predict": max_tokens},
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}]},
                      timeout=config.LLM_TIMEOUT)
    r.raise_for_status()
    return r.json()["message"]["content"]


CLOUD = {"anthropic": (_anthropic, lambda: config.ANTHROPIC_API_KEY),
         "openai": (_openai, lambda: config.OPENAI_API_KEY),
         "gemini": (_gemini, lambda: config.GEMINI_API_KEY)}


def ask_llm(system, user, temperature=0, max_tokens=800):
    """Send one system + user message and return the text reply."""
    global LAST_PROVIDER
    errors = []
    attempts = []
    if config.LLM_PROVIDER in CLOUD:
        func, key = CLOUD[config.LLM_PROVIDER]
        if key():
            attempts.append((config.LLM_PROVIDER, func))
        else:
            errors.append(f"{config.LLM_PROVIDER}: no API key in .env")
    attempts.append((f"ollama/{config.OLLAMA_MODEL}", _ollama))
    for name, func in attempts:
        try:
            text = func(system, user, temperature, max_tokens)
            LAST_PROVIDER = name
            return text.strip()
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            errors.append(f"{name}: {str(exc)[:160]}")
    raise LLMUnavailableError(
        "The AI assistant is unavailable. Add an API key to .env or start Ollama "
        f"(ollama serve; ollama pull {config.OLLAMA_MODEL}). Details: " + " | ".join(errors))


def llm_status():
    """What is configured and whether Ollama answers (for the sidebar)."""
    func_key = CLOUD.get(config.LLM_PROVIDER)
    try:
        ollama_up = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=1.5).ok
    except requests.RequestException:
        ollama_up = False
    return {"provider": config.LLM_PROVIDER,
            "provider_key_set": bool(func_key and func_key[1]()),
            "ollama_up": ollama_up, "ollama_model": config.OLLAMA_MODEL}
