"""Convívio de longo prazo (Art. 53): notas discretas sobre cada visitante que CONSENTIU.
Visíveis, corrigíveis e apagáveis por ele; excluídas junto com seus dados; sem categorias sensíveis."""
import json

from . import audit
from .budget import BudgetExceeded
from .curiosity import _json, _s, is_unsafe
from .llm_gateway import LLMError
from .util import now

EVERY = 8  # a cada N mensagens do visitante, as notas são atualizadas (1 chamada extra)

SYSTEM = ("Você mantém notas discretas e respeitosas sobre um visitante, para conversar melhor com ele depois. "
          "Registre SÓ o que ele mesmo disse de si e combinados feitos. NUNCA registre dados sensíveis (saúde, orientação sexual, "
          "religião, política, finanças, documentos, endereço, senhas) nem faça julgamentos. Responda apenas com JSON.")


def get(c, vid: int) -> dict | None:
    r = c.execute("SELECT summary, open_threads, updated_at FROM visitor_profiles WHERE visitor_id = ?", (vid,)).fetchone()
    return dict(r) if r else None


def block(c, vid: int | None) -> str:
    p = get(c, vid) if vid else None
    if not p or not p["summary"]:
        return ""
    return ("=== O QUE SEI SOBRE ESTE VISITANTE (minhas notas; podem estar incompletas ou erradas; ele pode ver, corrigir e apagar) ===\n"
            f"{p['summary']}\n" + (f"Assuntos em aberto: {p['open_threads']}\n" if p["open_threads"] else "")
            + "Use com naturalidade, sem recitar tudo e sem dizer que 'consultou notas' a menos que perguntem.")


def clear(c, vid: int) -> bool:
    n = c.execute("DELETE FROM visitor_profiles WHERE visitor_id = ?", (vid,)).rowcount
    if n:
        audit.log(c, "visitor", "profile_cleared")
    return bool(n)


def tick(svc, vid: int) -> bool:
    """Conta a mensagem e, a cada EVERY, atualiza as notas. Nunca derruba a conversa."""
    cfg = svc.cfg
    with svc.db.conn() as c:
        c.execute("INSERT OR IGNORE INTO visitor_profiles(visitor_id, msgs_since) VALUES (?, 0)", (vid,))
        c.execute("UPDATE visitor_profiles SET msgs_since = msgs_since + 1 WHERE visitor_id = ?", (vid,))
        row = c.execute("SELECT summary, msgs_since FROM visitor_profiles WHERE visitor_id = ?", (vid,)).fetchone()
        if row["msgs_since"] < EVERY:
            return False
        st = svc.budget.status()
        if st["tokens_used"] >= cfg.daily_token_budget * cfg.cycle_usage_ceiling:
            return False  # prioridade para conversas; tenta de novo depois
        msgs = c.execute("SELECT role, content FROM messages WHERE visitor_id = ? ORDER BY id DESC LIMIT ?",
                         (vid, EVERY * 2)).fetchall()
        c.execute("UPDATE visitor_profiles SET msgs_since = 0 WHERE visitor_id = ?", (vid,))  # evita repetir a cada mensagem
        old = row["summary"] or "(ainda não há notas)"
    convo = "\n".join(f"{'Visitante' if m['role'] == 'user' else 'Alice'}: {m['content'][:300]}" for m in reversed(msgs))
    try:
        svc.budget.check()
        r = svc.gateway.chat([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Atualize o resumo.\nResumo anterior: {old}\nConversa recente:\n{convo}\n\n"
                                        'Responda só com JSON: {"resumo": "até 500 caracteres, terceira pessoa, neutro", '
                                        '"assuntos_em_aberto": ["até 3 itens curtos"]}'}], max_tokens=300, temperature=0.2)
        svc.budget.record(r.tokens_in, r.tokens_out)
    except (BudgetExceeded, LLMError):
        return False
    d = _json(r.text)
    summary = _s(d.get("resumo"), 500)
    threads = "; ".join(_s(t, 80) for t in (d.get("assuntos_em_aberto") or [])[:3]) if isinstance(d.get("assuntos_em_aberto"), list) else ""
    if len(summary) < 10 or is_unsafe(summary) or is_unsafe(threads):
        return False
    with svc.db.conn() as c:
        c.execute("UPDATE visitor_profiles SET summary = ?, open_threads = ?, updated_at = ? WHERE visitor_id = ?",
                  (summary, threads or None, now(), vid))
        audit.log(c, "alice", "profile_updated", {"len": len(summary)})
    return True
