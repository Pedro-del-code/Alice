"""Auditoria com cadeia de hash (à prova de adulteração acidental ou silenciosa).
Nunca registre segredos nem o conteúdo das conversas."""
import hashlib
import json

from .util import now


def _digest(prev: str, ts: str, actor: str, action: str, detail: str, level: str) -> str:
    return hashlib.sha256("|".join((prev, ts, actor, action, detail, level)).encode()).hexdigest()


def log(c, actor: str, action: str, detail: dict | None = None, level: str = "info") -> None:
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")  # serializa a leitura do último hash com a escrita
    row = c.execute("SELECT hash FROM audit_log WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else ""
    ts, d = now(), json.dumps(detail or {}, ensure_ascii=False)
    c.execute(
        "INSERT INTO audit_log(ts, actor, action, detail, level, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
        (ts, actor, action, d, level, prev, _digest(prev, ts, actor, action, d, level)),
    )


def recent(c, limit: int = 100) -> list[dict]:
    rows = c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(limit, 500),)).fetchall()
    return [dict(r) for r in rows]


def verify(c) -> dict:
    """Recalcula a cadeia. Detecta alteração ou remoção de registros no meio do histórico."""
    prev, n = "", 0
    for r in c.execute("SELECT * FROM audit_log WHERE hash IS NOT NULL ORDER BY id"):
        expected = _digest(prev, r["ts"], r["actor"], r["action"], r["detail"] or "", r["level"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return {"ok": False, "checked": n, "broken_at": r["id"]}
        prev, n = r["hash"], n + 1
    return {"ok": True, "checked": n, "broken_at": None}
