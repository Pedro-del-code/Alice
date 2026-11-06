"""Iniciativa conversacional (Constituição Cap. XV, Art. 56–60).
A Alice pode falar PRIMEIRO, mas só com quem aceitou, por um motivo real, no máximo 1x/dia, nunca à noite,
nunca em pausa e NUNCA insistindo: enquanto a última mensagem proativa não for respondida, não manda outra.
Mensagens com culpa, 'saudade' cobrada, urgência ou dependência são descartadas por um filtro."""
import re
from datetime import datetime, timedelta, timezone

from . import audit, embeddings, emotions, memory, profiles
from .budget import BudgetExceeded
from .curiosity import _json, _s, is_unsafe
from .llm_gateway import LLMError
from .search import fold

MAX_LEN = 280
QUIET_START, QUIET_END = 22, 8  # horário de descanso (fuso do administrador)
INVESTIGATION_MATCH = 0.30

_GUILT = re.compile(
    r"(sinto (muito |tanta )?(a )?sua falta|saudades? de voce|nao me (deixe|abandone|ignore)|preciso (muito )?de voce|"
    r"estou (tao )?sozinha|voce (me )?(abandonou|esqueceu|ignorou)|por que (voce )?(nao )?(me )?(respond|ignora|ignorou)|"
    r"fiquei (triste|magoada)|se voce (sair|for embora)|volte logo|nao va embora|"
    r"(responda|volte) (agora|ja)|e urgente|so voce|ninguem (alem de voce|mais))")


def is_manipulative(text: str) -> bool:
    return bool(_GUILT.search(fold(text or "")))


def _iso(seconds: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def quiet_hours() -> bool:
    h = emotions.local_now().hour
    return h >= QUIET_START or h < QUIET_END


# ---------- mensagens de acompanhamento (dentro de uma conversa ativa) ----------
def queue_followup(c, cfg, vid: int, text: str) -> bool:
    text = _s(text, MAX_LEN)
    if not text or is_unsafe(text) or is_manipulative(text):
        audit.log(c, "alice", "followup_rejected", {}, level="warning")
        return False
    n = c.execute("SELECT COUNT(*) FROM proactive_messages WHERE visitor_id = ? AND kind = 'followup' AND created_at >= ?",
                  (vid, _iso(-3600))).fetchone()[0]
    if n >= cfg.followups_per_hour:
        return False
    delay = min(70, 20 + len(text) // 4)  # chega alguns segundos depois, como quem lembra de algo
    c.execute("INSERT INTO proactive_messages(visitor_id, kind, text, created_at, deliver_after, expires_at) VALUES (?, 'followup', ?, ?, ?, ?)",
              (vid, text, _iso(), _iso(delay), _iso(20 * 60)))
    return True


# ---------- caixa de entrada do visitante ----------
def inbox(c, vid: int) -> list[dict]:
    now_s = _iso()
    rows = c.execute("SELECT * FROM proactive_messages WHERE visitor_id = ? AND delivered_at IS NULL AND deliver_after <= ?"
                     " AND (expires_at IS NULL OR expires_at > ?) ORDER BY id", (vid, now_s, now_s)).fetchall()
    out = []
    for r in rows:
        c.execute("UPDATE proactive_messages SET delivered_at = ? WHERE id = ?", (now_s, r["id"]))
        c.execute("INSERT INTO messages(visitor_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)", (vid, r["text"], now_s))
        out.append({"id": r["id"], "kind": r["kind"], "text": r["text"], "created_at": r["created_at"]})
    return out


# ---------- iniciativa por conta própria ----------
def _eligible(c, cfg) -> list[dict]:
    rows = c.execute("SELECT * FROM visitors WHERE proactive = 1 AND last_seen >= ? AND (last_proactive_at IS NULL OR last_proactive_at <= ?)",
                     (_iso(-cfg.outreach_inactive_days * 86400), _iso(-cfg.outreach_min_gap_h * 3600))).fetchall()
    out = []
    for v in rows:
        last = c.execute("SELECT created_at FROM proactive_messages WHERE visitor_id = ? AND kind = 'outreach' ORDER BY id DESC LIMIT 1",
                         (v["id"],)).fetchone()
        if last and not c.execute("SELECT 1 FROM messages WHERE visitor_id = ? AND role = 'user' AND created_at > ? LIMIT 1",
                                  (v["id"], last["created_at"])).fetchone():
            continue  # a última não foi respondida: nunca insiste (Art. 57)
        n = c.execute("SELECT COUNT(*) FROM proactive_messages WHERE visitor_id = ? AND kind = 'outreach' AND created_at >= ?",
                      (v["id"], _iso(-86400))).fetchone()[0]
        if n < cfg.outreach_per_day:
            out.append(dict(v))
    return out


def _reason(c, vid: int) -> tuple[str, str] | None:
    """Motivo contextual real (Art. 56). Sem motivo, sem mensagem."""
    used = {r["seed"] for r in c.execute("SELECT seed FROM proactive_messages WHERE visitor_id = ? AND kind = 'outreach' AND created_at >= ?",
                                         (vid, _iso(-14 * 86400)))}
    prof = profiles.get(c, vid)
    if prof and prof.get("open_threads"):
        for t in prof["open_threads"].split(";"):
            t = t.strip()
            if t and f"thread:{t[:60]}" not in used:
                return f"thread:{t[:60]}", f"Ficou em aberto na conversa de vocês: {t}"
    if prof and prof.get("summary"):
        qv = memory._embed(prof["summary"])
        for inv in c.execute("SELECT i.id, t.title, i.conclusion FROM investigations i JOIN topics t ON t.id = i.topic_id ORDER BY i.id DESC LIMIT 6"):
            if f"inv:{inv['id']}" in used or not inv["conclusion"] or not qv:
                continue
            v = memory._embed(f"{inv['title']} {inv['conclusion']}")
            if v and embeddings.cosine(qv, v) >= INVESTIGATION_MATCH:
                return f"inv:{inv['id']}", (f"Algo que você andou pensando e que tem a ver com a pessoa: \"{inv['title']}\" — "
                                            f"{inv['conclusion'][:160]} (é uma hipótese sua, não um fato)")
    return None


def _compose(svc, name: str | None, summary: str, reason: str) -> str | None:
    system = ("Você é a Alice. Persona resumida:\n" + svc.identity.persona +
              "\nEscreva UMA mensagem curta (no máximo 2 frases, informal e natural) para puxar assunto com a pessoa por este motivo. "
              "Sem culpa, sem 'sinto sua falta', sem cobrar resposta, sem pressão, no máximo uma pergunta leve, fácil de ignorar. "
              'Responda só com JSON: {"mensagem": "..."}')
    user = f"Nome: {name or 'a pessoa'}. Notas sobre ela: {summary or '(poucas)'}\nMotivo para falar agora: {reason}"
    svc.budget.check()
    r = svc.gateway.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=200, temperature=0.8)
    svc.budget.record(r.tokens_in, r.tokens_out)
    text = _s(_json(r.text).get("mensagem"), MAX_LEN)
    return text if text and not is_unsafe(text) and not is_manipulative(text) else None


def run_outreach(svc, actor: str = "alice", max_per_tick: int = 3) -> dict:
    cfg, db = svc.cfg, svc.db
    if not cfg.proactive:
        return {"skipped": "disabled"}
    if svc.is_paused():
        return {"skipped": "paused"}
    if quiet_hours():
        return {"skipped": "quiet_hours"}
    if svc.budget.status()["tokens_used"] >= cfg.daily_token_budget * cfg.cycle_usage_ceiling:
        return {"skipped": "usage_ceiling"}  # prioridade para conversas
    plan = []
    with db.conn() as c:
        for v in _eligible(c, cfg)[:max_per_tick * 3]:
            r = _reason(c, v["id"])
            if r:
                prof = profiles.get(c, v["id"]) or {}
                plan.append((v["id"], v["display_name"], prof.get("summary") or "", r))
            if len(plan) >= max_per_tick:
                break
    queued = rejected = 0
    for vid, name, summary, (seed, reason) in plan:
        try:
            text = _compose(svc, name, summary, reason)
        except (BudgetExceeded, LLMError):
            break
        with db.conn() as c:
            if not text:
                rejected += 1
                audit.log(c, actor, "outreach_rejected", {}, level="warning")
                continue
            c.execute("INSERT INTO proactive_messages(visitor_id, kind, text, seed, created_at, deliver_after, expires_at) VALUES (?, 'outreach', ?, ?, ?, ?, ?)",
                      (vid, text, seed, _iso(), _iso(), _iso(7 * 86400)))
            c.execute("UPDATE visitors SET last_proactive_at = ? WHERE id = ?", (_iso(), vid))
            audit.log(c, actor, "outreach_queued", {"seed": seed.split(":")[0]})  # só o tipo; nunca o texto
            queued += 1
    return {"eligible": len(plan), "queued": queued, "rejected": rejected}
