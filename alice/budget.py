"""Limites de recursos: requisições/minuto e tokens/dia (protege a cota do Groq)."""
import time
from collections import deque

from .util import today


class BudgetExceeded(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind  # "rpm" | "daily_tokens"


class Budget:
    def __init__(self, db, daily_tokens: int, rpm: int):
        self.db, self.daily_tokens, self.rpm = db, daily_tokens, rpm
        self._calls = deque()

    def check(self) -> None:
        t = time.monotonic()
        while self._calls and t - self._calls[0] > 60:
            self._calls.popleft()
        if len(self._calls) >= self.rpm:
            raise BudgetExceeded("rpm")
        with self.db.conn() as c:
            r = c.execute("SELECT tokens_in + tokens_out AS t FROM usage WHERE day = ?", (today(),)).fetchone()
        if r and r["t"] >= self.daily_tokens:
            raise BudgetExceeded("daily_tokens")
        self._calls.append(t)

    def record(self, tokens_in: int, tokens_out: int) -> None:
        with self.db.conn() as c:
            c.execute(
                "INSERT INTO usage(day, requests, tokens_in, tokens_out) VALUES (?,1,?,?)"
                " ON CONFLICT(day) DO UPDATE SET requests = requests + 1,"
                " tokens_in = tokens_in + excluded.tokens_in, tokens_out = tokens_out + excluded.tokens_out",
                (today(), tokens_in, tokens_out))

    def status(self) -> dict:
        with self.db.conn() as c:
            r = c.execute("SELECT * FROM usage WHERE day = ?", (today(),)).fetchone()
        used = (r["tokens_in"] + r["tokens_out"]) if r else 0
        return {"day": today(), "requests": r["requests"] if r else 0, "tokens_used": used,
                "daily_token_budget": self.daily_tokens, "rpm_limit": self.rpm}
