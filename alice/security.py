"""Fase 6 — Cabeçalhos de segurança, CSP com nonce e limitadores em memória.
IPs são usados só transitoriamente, em memória, para proteger cota e login admin; nunca são gravados (Art. 30)."""
import secrets
import time
from collections import defaultdict, deque

from flask import g, request


class RateLimiter:
    def __init__(self, limit: int, window: float = 60.0):
        self.limit, self.window, self.hits = limit, window, defaultdict(deque)

    def allow(self, key: str) -> bool:
        t, q = time.monotonic(), self.hits[key]
        while q and t - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(t)
        if len(self.hits) > 5000:  # evita crescimento ilimitado
            self.hits.clear()
        return True


class FailureLimiter:
    """Bloqueia tentativas de login admin: 5 falhas em 15 min → 15 min de bloqueio."""
    def __init__(self, max_fail: int = 5, window: float = 900.0):
        self.max_fail, self.window, self.fails = max_fail, window, defaultdict(deque)

    def blocked(self, key: str) -> bool:
        t, q = time.monotonic(), self.fails[key]
        while q and t - q[0] > self.window:
            q.popleft()
        return len(q) >= self.max_fail

    def fail(self, key: str) -> None:
        self.fails[key].append(time.monotonic())

    def ok(self, key: str) -> None:
        self.fails.pop(key, None)


def new_nonce() -> str:
    g.nonce = secrets.token_urlsafe(16)
    return g.nonce


def apply_headers(resp):
    nonce = getattr(g, "nonce", None)
    script = f"'nonce-{nonce}'" if nonce else "'none'"
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; script-src {script}; style-src {script}; img-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"  # Art. 34: sem câmera/microfone
    if request.path == "/api/avatar.svg":  # SVG estático com <style> embutido, sem scripts
        resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    if request.is_secure:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path.startswith("/api/") and request.path != "/api/avatar.svg":
        resp.headers["Cache-Control"] = "no-store"
    return resp
