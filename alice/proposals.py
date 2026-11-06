"""Propostas de autoalteração. A Alice PROPÕE; um humano DECIDE (Art. 24 e 37).
Tipos permitidos: mudança de personalidade (estilo/interesses/preferências) e objetivo intelectual.
Nunca existe tipo para Constituição, permissões, código ou segurança."""
import json

from . import audit, emotions, goals
from .curiosity import is_unsafe
from .identity import ALLOWED_TRAIT_KEYS, IdentityError
from .util import now

MAX_PENDING = 5


def create(c, kind: str, payload: dict, rationale: str, source: str = "alice") -> int | None:
    rationale = " ".join((rationale or "").split())[:400]
    if kind not in ("personality", "goal") or not rationale or not isinstance(payload, dict):
        return None
    blob = json.dumps(payload, ensure_ascii=False)
    if is_unsafe(rationale) or is_unsafe(blob) or len(blob) > 1500:
        audit.log(c, source, "proposal_rejected", {"kind": kind, "reason": "unsafe_or_long"}, level="warning")
        return None
    if kind == "goal":  # objetivos são só intelectuais: entram direto (limite de 3), visíveis e revogáveis
        gid = goals.add_goal(c, payload.get("titulo", ""), payload.get("porque", rationale), source)
        if not gid:
            return None
        cur = c.execute("INSERT INTO proposals(kind, payload, rationale, status, decision_note, created_at, decided_at)"
                        " VALUES ('goal',?,?, 'approved','automática (objetivo intelectual)',?,?)",
                        (blob, rationale, now(), now()))
        return cur.lastrowid
    traits = payload.get("traits")
    if not isinstance(traits, dict) or not traits or set(traits) - ALLOWED_TRAIT_KEYS:
        return None
    if c.execute("SELECT COUNT(*) FROM proposals WHERE status = 'pending'").fetchone()[0] >= MAX_PENDING:
        return None
    if c.execute("SELECT 1 FROM proposals WHERE status = 'pending' AND payload = ?", (blob,)).fetchone():
        return None
    cur = c.execute("INSERT INTO proposals(kind, payload, rationale, created_at) VALUES (?,?,?,?)",
                    (kind, blob, rationale, now()))
    audit.log(c, source, "proposal_created", {"id": cur.lastrowid, "kind": kind})
    return cur.lastrowid


def list_(c, status: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM proposals", []
    if status:
        q += " WHERE status = ?"; a.append(status)
    out = []
    for r in c.execute(q + " ORDER BY id DESC LIMIT 100", a).fetchall():
        d = dict(r); d["payload"] = json.loads(d["payload"]); out.append(d)
    return out


def decide(svc, pid: int, approve: bool, note: str = "", actor: str = "admin") -> dict:
    with svc.db.conn() as c:
        r = c.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
    if not r:
        raise ValueError("proposta não encontrada")
    if r["status"] != "pending":
        raise ValueError("proposta já decidida")
    result = {}
    if approve and r["kind"] == "personality":
        try:  # aplicada pelo mesmo caminho validado da evolução manual (versão + justificativa + auditoria)
            result["personality_version"] = svc.identity.evolve(json.loads(r["payload"])["traits"], r["rationale"], actor=f"approved-by-{actor}")
        except IdentityError as e:
            raise ValueError(str(e)) from None
    with svc.db.conn() as c:
        c.execute("UPDATE proposals SET status = ?, decision_note = ?, decided_at = ? WHERE id = ?",
                  ("approved" if approve else "rejected", (note or "")[:300], now(), pid))
        audit.log(c, actor, "proposal_approved" if approve else "proposal_rejected_by_admin", {"id": pid})
        emotions.appraise(c, "proposta_aprovada" if approve else "proposta_rejeitada")
    return {"id": pid, "approved": approve, **result}
