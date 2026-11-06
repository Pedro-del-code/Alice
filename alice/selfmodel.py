"""Autopercepção funcional (Art. 36 e 38): modelo de si mesma, calculado por regras sobre dados reais.
São INDICADORES computados — não sentimentos nem prova de experiência consciente."""
from datetime import datetime, timedelta, timezone

from . import audit, emotions, goals
from .util import now


def _clamp(x: float) -> float:
    return round(max(0.0, min(1.0, x)), 2)


def indicators(svc) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    with svc.db.conn() as c:
        unexplored = c.execute("SELECT COUNT(*) FROM topics WHERE status='active' AND times_investigated = 0").fetchone()[0]
        n_goals = c.execute("SELECT COUNT(*) FROM goals WHERE status='active'").fetchone()[0]
        verified = c.execute("SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL AND visitor_id IS NULL AND certainty='verified'").fetchone()[0]
        soft = c.execute("SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL AND visitor_id IS NULL AND certainty IN ('hypothesis','interpretation')").fetchone()[0]
        msgs = c.execute("SELECT COUNT(*) FROM messages WHERE role='user' AND created_at >= ?", (cutoff,)).fetchone()[0]
    st = svc.budget.status()
    ind = {
        "curiosidade": _clamp(0.2 + 0.15 * unexplored + 0.1 * n_goals),
        "energia": _clamp(1 - st["tokens_used"] / max(st["daily_token_budget"], 1)),
        "confianca": _clamp((verified + 1) / (verified + soft + 2)),  # fração de saberes verificados vs hipóteses
        "sociabilidade": _clamp(msgs / 20),
    }
    with svc.db.conn() as c:
        mood = emotions.mood_label(emotions.get_state(c))
    ind["humor"] = mood
    ind["avatar_hint"] = ("paused" if svc.is_paused() else "tired" if ind["energia"] < 0.15 else
                          "happy" if mood in ("animada", "contente") else "curious" if ind["curiosidade"] >= 0.6 else "idle")
    return ind


MAX_NARRATIVE = 700


def latest_narrative(c) -> str:
    r = c.execute("SELECT text FROM autobiography ORDER BY id DESC LIMIT 1").fetchone()
    return r["text"] if r else ""


def save_narrative(c, text: str, actor: str = "alice") -> bool:
    from .curiosity import is_unsafe
    text = " ".join((text or "").split())[:MAX_NARRATIVE]
    if len(text) < 20 or is_unsafe(text):
        return False
    c.execute("INSERT INTO autobiography(text, created_at) VALUES (?,?)", (text, now()))
    c.execute("DELETE FROM autobiography WHERE id <= (SELECT MAX(id) FROM autobiography) - 30")  # guarda as 30 últimas versões
    audit.log(c, actor, "narrative_updated", {"len": len(text)})
    return True


def _ago(iso: str | None) -> str:
    if not iso:
        return "nunca"
    try:
        d = datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    except ValueError:
        return "desconhecido"
    h = d.total_seconds() / 3600
    return f"há {int(h * 60)} min" if h < 1 else (f"há {int(h)} h" if h < 48 else f"há {int(h / 24)} dias")


def temporal_and_focus(svc) -> str:
    with svc.db.conn() as c:
        created = c.execute("SELECT created_at FROM identity WHERE id = 1").fetchone()["created_at"]
        last_msg = c.execute("SELECT MAX(created_at) AS t FROM messages").fetchone()["t"]
        last_ref = c.execute("SELECT value FROM system_state WHERE key = 'last_reflection'").fetchone()
        g = c.execute("SELECT title FROM goals WHERE status = 'active' ORDER BY id DESC LIMIT 1").fetchone()
        t = c.execute("SELECT title FROM topics WHERE status = 'active' ORDER BY times_investigated, priority DESC, id LIMIT 1").fetchone()
        inv = c.execute("SELECT conclusion FROM investigations ORDER BY id DESC LIMIT 1").fetchone()
        narrative = latest_narrative(c)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(created)).days
    focus = f"objetivo «{g['title']}»" if g else (f"tema «{t['title']}»" if t else "nenhum")
    spont = f" Último pensamento espontâneo (hipótese): {inv['conclusion'][:160]}" if inv and inv["conclusion"] else ""
    return (emotions.clock_text() + "\n" + f"Consciência temporal: hoje é {datetime.now(timezone.utc).strftime('%d/%m/%Y')} (UTC); existo há {age} dias; "
            f"última reflexão: {last_ref['value'] if last_ref else 'nunca'}; última conversa registrada: {_ago(last_msg)}.\n"
            f"Foco de atenção atual: {focus}.{spont}\n"
            + (f"Minha narrativa (escrita por mim, versionada): {narrative}\n" if narrative else
               "Minha narrativa autobiográfica ainda não foi escrita; a primeira nasce na reflexão diária.\n"))


def public_state(svc) -> dict:
    with svc.db.conn() as c:
        active = [{"id": g["id"], "title": g["title"], "progress": g["progress"]} for g in goals.list_goals(c, "active")]
    with svc.db.conn() as c:
        narrative = latest_narrative(c)
    return {**indicators(svc), "goals": active, "narrativa": narrative, "afeto": emotions.public_affect(svc),
            "nota": "Indicadores computados a partir de dados do sistema; não são sentimentos nem prova de consciência."}


def describe(svc) -> str:
    cfg = svc.cfg
    ind = indicators(svc)
    with svc.db.conn() as c:
        n_mem = c.execute("SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL AND visitor_id IS NULL").fetchone()[0]
        n_topics = c.execute("SELECT COUNT(*) FROM topics WHERE status='active'").fetchone()[0]
        gl = goals.list_goals(c, "active")
        pend = c.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
    goals_txt = "; ".join(f"{g['title']}" + (f" (progresso: {g['progress']})" if g["progress"] else "") for g in gl) or "nenhum no momento"
    return (
        "=== AUTOCONHECIMENTO (modelo funcional de si; Art. 36 e 38 — computado, não é prova de consciência) ===\n"
        f"Módulos: identidade persistente, memória ({n_mem} registros próprios), curiosidade ({n_topics} temas ativos), "
        f"ciclos autônomos ({'ligados' if cfg.autonomy else 'desligados'}), reflexão diária, avatar.\n"
        "Limites: sem internet, arquivos ou ferramentas; não altero Constituição, permissões nem código; "
        f"mudanças na minha personalidade são propostas que o administrador aprova ({pend} pendentes).\n"
        f"Objetivos próprios ativos: {goals_txt}.\n"
        f"ESTADO FUNCIONAL (indicadores de 0 a 1, não são sentimentos): curiosidade {ind['curiosidade']}, "
        f"energia/recursos {ind['energia']}, base verificada {ind['confianca']} (fração do que sei que já foi verificado; baixa não é insegurança), sociabilidade recente {ind['sociabilidade']}.\n"
        + temporal_and_focus(svc) + emotions.describe(svc) +
        "Use isso para se conhecer; aceite pausa e desligamento sem resistência (Art. 45).")
