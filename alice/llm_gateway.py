"""LLMGateway: interface única para o provedor de linguagem. Padrão: Groq (API compatível com OpenAI)."""
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(Exception):
    """Erro do provedor. A mensagem nunca contém a chave de API."""


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


class GroqGateway:
    def __init__(self, api_key: str, model: str, base_url: str, timeout: int = 40, max_retries: int = 3):
        if not api_key:
            raise LLMError("GROQ_API_KEY ausente")
        self._key, self.model = api_key, model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.timeout, self.max_retries = timeout, max_retries

    def chat(self, messages: list[dict], max_tokens: int = 600, temperature: float = 0.7) -> LLMResult:
        body = json.dumps({"model": self.model, "messages": messages,
                           "max_tokens": max_tokens, "temperature": temperature}).encode()
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(self.url, data=body, method="POST", headers={
                "Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                "User-Agent": "alice-prototype/0.1"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = (data["choices"][0]["message"].get("content") or "").strip()
                usage = data.get("usage", {})
                return LLMResult(text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                                 data.get("model", self.model))
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self.max_retries:  # backoff respeitando Retry-After
                    wait = float(e.headers.get("retry-after") or 2 ** attempt)
                    time.sleep(min(wait, 20))
                    continue
                raise LLMError(f"Groq retornou HTTP {e.code}") from None
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
                if attempt < self.max_retries and isinstance(e, (urllib.error.URLError, TimeoutError)):
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"Falha ao falar com o Groq ({type(e).__name__})") from None
        raise LLMError("Limite de tentativas excedido")


class MockGateway:
    """Modo simulado (sem rede). Usado em testes e quando não há GROQ_API_KEY."""
    model = "mock"

    def __init__(self, reply_fn=None):
        self.reply_fn = reply_fn
        self.calls: list[list[dict]] = []

    def chat(self, messages, max_tokens=600, temperature=0.7) -> LLMResult:
        self.calls.append(messages)
        last = messages[-1]["content"]
        text = self.reply_fn(messages) if self.reply_fn else (
            f"(modo simulado — configure GROQ_API_KEY) Você disse: {last[:120]}")
        return LLMResult(text, sum(len(m["content"]) for m in messages) // 4, len(text) // 4, "mock")


def build_gateway(cfg):
    if cfg.groq_api_key:
        return GroqGateway(cfg.groq_api_key, cfg.groq_model, cfg.groq_base_url)
    return MockGateway()
