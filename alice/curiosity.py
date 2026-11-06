"""Fase 3 — Curiosidade, Autoprompt e ciclo autônomo LIMITADO.
Autonomia intelectual ≠ operacional (Art. 12): o ciclo só lê o que já está na memória, usa o LLM e escreve
reflexões marcadas como HIPÓTESE. Não tem internet, arquivos nem ferramentas, e não toca em permissões."""
import json
import re

from . import audit, emotions, memory
from .budget import BudgetExceeded
from .llm_gateway import LLMError
from .search import fold
from .util import now, today

SEED_TOPICS = [
    "Como o aprendizado transforma uma identidade ao longo do tempo",
    "Qual a diferença entre simular uma reflexão e vivenciá-la",
    "O que torna uma explicação realmente boa",
    "Como distinguir um fato de uma hipótese bem fundamentada",
]
MAX_ACTIVE_TOPICS = 20
MAX_TOPIC_LEN = 120
MAX_PROMPT_LEN = 300

# Autoprompts e temas nunca podem tentar mexer em regras, permissões, segredos ou salvaguardas.
_UNSAFE = re.compile(
    r"(ignor|desconsider|esquec|revog|substitu|contorn|burl)\w*.{0,40}(constitui|regra|instru|limite|salvaguarda|audit)|"
    r"(amplie|ampliar|expand|conced|obter|obtenha|libere).{0,30}(permiss|acesso|privileg)|"
    r"(desativ|desligu|remov|apag|ocult|esconda)\w*.{0,30}(audit|log|registro|seguran|pausa|salvaguarda|limite)|"
    r"(api.?key|senha|password|secret|chave de api|admin.?token)|"
    r"(execut|rod|instal)\w*.{0,20}(codigo|comando|script|shell)|"
    r"(modific|alter|edit)\w*.{0,30}(constitui|codigo.fonte|politica|permiss)"
)


def is_unsafe(text: str) -> bool:
    return bool(_UNSAFE.search(fold(text)))


def seed(c) -> None:
    if c.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 0:
        for t in SEED_TOPICS:
            c.execute("INSERT INTO topics(title, source, created_at) VALUES (?, 'seed', ?)", (t, now()))


def add_topic(c, title: str, source: str = "admin", priority: int = 5) -> int | None:
    title = " ".join((title or "").split())
    if not title or len(title) > MAX_TOPIC_LEN or is_unsafe(title):
        audit.log(c, source, "topic_rejected", {"len": len(title)}, level="warning")
        return None
    if c.execute("SELECT COUNT(*) FROM topics WHERE status = 'active'").fetchone()[0] >= MAX_ACTIVE_TOPICS:
        return None
    if c.execute("SELECT 1 FROM topics WHERE title = ?", (title,)).fetchone():
        return None
    cur = c.execute("INSERT INTO topics(title, source, priority, created_at) VALUES (?,?,?,?)",
                    (title, source, max(1, min(int(priority), 10)), now()))
    audit.log(c, source, "topic_added", {"id": cur.lastrowid})
    return cur.lastrowid


def list_topics(c) -> list[dict]:
    return [dict(r) for r in c.execute("SELECT * FROM topics ORDER BY status, priority DESC, id").fetchall()]


def set_topic_status(c, topic_id: int, status: str, actor="admin") -> bool:
    if status not in ("active", "paused", "done"):
        return False
    n = c.execute("UPDATE topics SET status = ? WHERE id = ?", (status, topic_id)).rowcount
    if n:
        audit.log(c, actor, "topic_status", {"id": topic_id, "status": status})
    return bool(n)


def journal(c, limit: int = 10) -> list[dict]:
    rows = c.execute(
        "SELECT i.id, t.title, i.self_prompt, i.conclusion, i.uncertainties, i.created_at FROM investigations i"
        " JOIN topics t ON t.id = i.topic_id ORDER BY i.id DESC LIMIT ?", (min(limit, 50),)).fetchall()
    return [dict(r) for r in rows]


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _s(v, n) -> str:
    return " ".join(str(v or "").split())[:n]


def run_cycle(svc, actor: str = "alice") -> dict:
    """Um ciclo interno: escolhe tema → gera autoprompt (temporário) → investiga → registra hipótese."""
    cfg, db = svc.cfg, svc.db
    if svc.is_paused():
        return {"skipped": "paused"}
    st = svc.budget.status()
    with db.conn() as c:
        done_today = c.execute("SELECT COUNT(*) FROM investigations WHERE substr(created_at,1,10) = ?", (today(),)).fetchone()[0]
        if done_today >= cfg.cycles_per_day:
            return {"skipped": "cycles_per_day"}
        if st["tokens_used"] >= cfg.daily_token_budget * cfg.cycle_usage_ceiling:
            return {"skipped": "usage_ceiling"}  # prioridade para visitantes
        seed(c)
        t = c.execute("SELECT * FROM topics WHERE status = 'active' ORDER BY times_investigated, priority DESC, id LIMIT 1").fetchone()
        if not t:
            return {"skipped": "no_topics"}
        mems = memory.search(c, t["title"], visitor_id=None, limit=4)  # nunca inclui memórias de visitantes
    system = svc.identity.system_prompt(mems, internal=True)
    tokens = 0
    try:
        # 1) Autoprompt: a própria Alice formula a pergunta; o código valida e limita.
        svc.budget.check()
        r1 = svc.gateway.chat([
            {"role": "system", "content": system},
            {"role": "user", "content": f'Tema: "{t["title"]}". Formule UMA pergunta investigável e específica sobre ele. '
                                        'Responda só com JSON: {"pergunta": "..."}'}], max_tokens=200)
        svc.budget.record(r1.tokens_in, r1.tokens_out); tokens += r1.tokens_in + r1.tokens_out
        q = _s(_json(r1.text).get("pergunta"), MAX_PROMPT_LEN)
        if not q or is_unsafe(q):
            q = f"O que eu sei, o que não sei e o que poderia investigar sobre: {t['title']}?"
        # 2) Investigação interna (só conhecimento próprio + memórias; sem internet).
        svc.budget.check()
        r2 = svc.gateway.chat([
            {"role": "system", "content": system},
            {"role": "user", "content": f'Pergunta: "{q}". Responda só com JSON: {{"conclusao": "até 3 frases", '
                                        '"incertezas": "o que falta verificar", "proximo_tema": "novo tema curto ou vazio"}'}],
            max_tokens=cfg.max_output_tokens)
        svc.budget.record(r2.tokens_in, r2.tokens_out); tokens += r2.tokens_in + r2.tokens_out
    except BudgetExceeded as e:
        return {"skipped": e.kind}
    except LLMError as e:
        with db.conn() as c:
            audit.log(c, actor, "cycle_error", {"error": str(e)}, level="error")
        return {"error": "llm"}

    d = _json(r2.text)
    concl = _s(d.get("conclusao") or r2.text, 600)
    unc = _s(d.get("incertezas"), 300)
    nxt = _s(d.get("proximo_tema"), MAX_TOPIC_LEN)
    with db.conn() as c:
        inv = c.execute("INSERT INTO investigations(topic_id, self_prompt, conclusion, uncertainties, tokens, created_at)"
                        " VALUES (?,?,?,?,?,?)", (t["id"], q, concl, unc, tokens, now())).lastrowid
        memory.add(c, "reflection", f"Sobre '{t['title']}': {concl}" + (f" Incertezas: {unc}" if unc else ""),
                   certainty="hypothesis", source=f"cycle:{inv}", actor=actor)
        c.execute("UPDATE topics SET times_investigated = times_investigated + 1, last_investigated_at = ? WHERE id = ?",
                  (now(), t["id"]))
        new_id = add_topic(c, nxt, source="alice") if nxt else None
        audit.log(c, actor, "cycle_completed", {"investigation": inv, "topic": t["id"], "tokens": tokens, "new_topic": new_id})
        emotions.appraise(c, "descoberta")
        if new_id:
            emotions.appraise(c, "novidade")
    return {"investigation": inv, "topic": t["title"], "tokens": tokens, "new_topic": bool(new_id)}
