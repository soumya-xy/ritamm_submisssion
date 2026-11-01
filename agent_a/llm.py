"""OpenAI-compatible chat client (plain httpx, no vendor SDK).

Provider, model and key all come from the environment. Retries 429/5xx with
back-off (honouring Retry-After). Auth / model-not-found errors are fatal and
never silently replaced by a fallback: the brief requires the LLM to be used.
"""
from __future__ import annotations

import re
import time

import httpx

from common import jsonio


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 120.0, max_attempts: int = 6):
        if not (api_key and base_url and model):
            raise LLMError("LLM_API_KEY, LLM_BASE_URL and LLM_MODEL must all be set (see .env.example)")
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self.timeout, self.max_attempts = timeout, max_attempts
        self._json_mode = True
        self.calls = 0

    def chat_json(self, system: str, user: str) -> dict:
        """Send one prompt, return the parsed JSON object from the reply."""
        body = {"model": self.model, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        delay = 2.0
        for attempt in range(1, self.max_attempts + 1):
            payload = dict(body)
            if self._json_mode:
                payload["response_format"] = {"type": "json_object"}
            try:
                resp = httpx.post(f"{self.base_url}/chat/completions", json=payload, timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.HTTPError as exc:
                if attempt == self.max_attempts:
                    raise LLMError(f"LLM request failed: {exc}") from exc
                time.sleep(delay); delay = min(delay * 2, 30); continue
            self.calls += 1
            if resp.status_code == 200:
                try:
                    content = resp.json()["choices"][0]["message"]["content"]
                    return extract_json(content)
                except (KeyError, IndexError, ValueError) as exc:
                    if attempt == self.max_attempts:
                        raise LLMError(f"Unparseable LLM reply: {exc}") from exc
                    continue  # temperature 0 but providers differ; ask again
            if resp.status_code == 400 and self._json_mode and "response_format" in resp.text:
                self._json_mode = False      # provider does not support JSON mode; rely on the prompt
                continue
            if resp.status_code in (401, 403, 404):
                raise LLMError(f"LLM rejected the request (HTTP {resp.status_code}): {resp.text[:300]}")
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_attempts:
                wait = float(resp.headers.get("retry-after", delay) or delay)
                time.sleep(min(wait, 60)); delay = min(delay * 2, 30); continue
            raise LLMError(f"LLM error HTTP {resp.status_code}: {resp.text[:300]}")
        raise LLMError("LLM retries exhausted")


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a reply, tolerating code fences / chatter."""
    if text is None:
        raise ValueError("empty reply")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        obj = jsonio.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in reply")
        obj = jsonio.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("reply JSON is not an object")
    return obj
