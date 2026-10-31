"""OpenRouter chat client (OpenAI-compatible) with an on-disk cache and cost accounting.

Configuration via environment or a `.env` file in the working directory:
  OPENROUTER_API_KEY   required for any LLM call
  LOCQA_MODEL          model for bulk review   (default anthropic/claude-sonnet-5.5)
  LOCQA_FIX_MODEL      model for fix proposals (default anthropic/claude-opus-5.5)
  LOCQA_CACHE_DIR      response cache          (default .llm_cache)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"  # alpha: path taken from the official openrouter SDK 1.3.106
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_FIX_MODEL = "anthropic/claude-opus-5.5"


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Za-z_]\w*)\s*=\s*(.*?)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            os.environ.setdefault(m.group(1), m.group(2).strip("'\""))


class LlmUnavailable(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    cached_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    by_model: dict = field(default_factory=dict)

    def add(self, model: str, u: dict, cached: bool):
        self.calls += 1
        if cached:
            self.cached_calls += 1
        pt, ct, cost = u.get("prompt_tokens", 0), u.get("completion_tokens", 0), float(u.get("cost") or 0)
        self.prompt_tokens += pt
        self.completion_tokens += ct
        self.cost_usd += cost
        m = self.by_model.setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})
        m["calls"] += 1
        m["prompt_tokens"] += pt
        m["completion_tokens"] += ct
        m["cost_usd"] += cost

    def as_dict(self) -> dict:
        return {"calls": self.calls, "cached_calls": self.cached_calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "cost_usd": round(self.cost_usd, 4), "by_model": self.by_model}


class Client:
    def __init__(self, model: str | None = None, cache_dir: str | None = None):
        load_dotenv()
        self.key = os.environ.get("OPENROUTER_API_KEY")
        self.model = model or os.environ.get("LOCQA_MODEL") or DEFAULT_MODEL
        self.cache = Path(cache_dir or os.environ.get("LOCQA_CACHE_DIR") or ".llm_cache")
        self.usage = Usage()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def chat_json(self, system: str, user: str, model: str | None = None, max_tokens: int = 4000) -> dict:
        """One chat completion that must return a JSON object. Cached by (model, prompts).
        A broken or truncated answer is retried once with an explicit request for valid JSON."""
        try:
            return self._chat_json(system, user, model, max_tokens)
        except (ValueError, KeyError, IndexError):
            fix = ("\n\nВАЖНО: предыдущий ответ был невалидным или обрезанным JSON. Верни строго валидный JSON по схеме, "
                   "без кода и выражений внутри строк; если находок много, сократи evidence до одного предложения.")
            return self._chat_json(system, user + fix, model, max(16000, max_tokens * 2))

    def _chat_json(self, system: str, user: str, model: str | None, max_tokens: int) -> dict:
        model = model or self.model
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }
        h = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
        path = self.cache / f"{h}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.usage.add(model, data.get("usage", {}), cached=True)
            return _extract_json(data)
        if not self.key:
            raise LlmUnavailable("OPENROUTER_API_KEY is not set (put it into .env)")
        data = self._post(body)
        self.cache.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.usage.add(model, data.get("usage", {}), cached=False)
        return _extract_json(data)

    def decide(self, model: str, state, questions: dict) -> dict:
        """OpenRouter Decisions API: typed questions in, calibrated answers out. Returns the `answers` dict."""
        body = {"model": model, "state": state, "questions": questions}
        h = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
        path = self.cache / f"d_{h}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.usage.add(model, _decision_usage(data), cached=True)
            return data["answers"]
        if not self.key:
            raise LlmUnavailable("OPENROUTER_API_KEY is not set (put it into .env)")
        data = self._post(body, url=DECISIONS_URL)
        self.cache.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.usage.add(model, _decision_usage(data), cached=False)
        return data["answers"]

    def _post(self, body: dict, retries: int = 4, url: str = API_URL) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                     "X-Title": "locqa"},
        )
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2 ** attempt * 3)
                    continue
                raise RuntimeError(f"OpenRouter HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}") from e
            except urllib.error.URLError:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt * 3)
                    continue
                raise
        raise RuntimeError("unreachable")


def _decision_usage(data: dict) -> dict:
    u = data.get("usage") or {}
    return {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0), "cost": u.get("cost")}


def _extract_json(data: dict) -> dict:
    text = data["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"model did not return JSON: {text[:200]}")
    return json.loads(m.group(0))
