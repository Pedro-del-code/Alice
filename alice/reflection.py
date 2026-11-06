"""Reflexão de consolidação (Art. 8 e 38) — "sono" computacional, no máximo uma vez por dia.
Sintetiza o que aprendeu, REVISA crenças próprias que encontrar erradas (com histórico), atualiza objetivos
e pode PROPOR mudança de personalidade (que o administrador aprova). Só lê memórias dela, nunca de visitantes."""
import json

from . import audit, emotions, goals, memory, proposals, selfmodel
from .budget import BudgetExceeded
from .curiosity import _json, _s, is_unsafe
from .llm_gateway import LLMError
from .util import now, today

MAX_REVISIONS = 2


def _self_memories(c, limit=12) -> list[dict]:
    rows = c.execute(
        "SELECT * FROM memories WHERE deleted_at IS NULL AND visitor_id IS NULL AND category IN ('reflection','learning')"
        " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _is_own(m: dict) -> bool:
    """Só crenças que a própria Alice formou podem ser revisadas por ela; fatos do admin ficam intocados."""
    return (m.get("source") or "").startswith(("cycle:", "self", "reflection:"))


def run_reflection(svc, actor: str = "alice", force: bool = False) -> dict:
    cfg, db = svc.cfg, svc.db
    if svc.is_paused():
        return {"skipped": "paused"}
    st = svc.budget.status()
    with db.conn() as c:
        last = c.execute("SELECT value FROM system_state WHERE key='last_reflection'").fetchone()
        if not force and last and last["value"] == today():
            return {"skipped": "already_today"}
        if not force and st["tokens_used"] >= cfg.daily_token_budget * cfg.cycle_usage_ceiling:
            return {"skipped": "usage_ceiling"}
        mems = _self_memories(c)
        if not mems:
            return {"skipped": "nothing_to_reflect"}
        gl = goals.list_goals(c, "active")
        pers = svc.identity.personality()
        old_narr = selfmodel.latest_narrative(c)
    ids = {m["id"]: m for m in mems}
    lines = "\n".join(f"{m['id']}: [{m['certainty']}] {m['content']}" for m in mems)
    goal_lines = "\n".join(f"{g['id']}: {g['title']}" for g in gl) or "(nenhum)"
    prompt = (
        "Faça uma REFLEXÃO de consolidação (processo computacional interno, sem visitante).\n"
        f"Memórias recentes suas (id: texto):\n{lines}\n\nObjetivos ativos:\n{goal_lines}\n\n"
        f"Personalidade atual: {json.dumps(pers['traits'], ensure_ascii=False)}\n"
        f"Narrativa atual sobre mim: {old_narr or '(ainda não escrita)'}\n\n"
        'Responda só com JSON: {"sintese": "até 3 frases ligando as ideias", '
        '"revisoes": [{"id": N, "novo_texto": "...", "motivo": "..."}], '
        '"proposta_personalidade": {"traits": {"estilo": "..."}, "justificativa": "..."} ou null, '
        '"novo_objetivo": {"titulo": "...", "porque": "..."} ou null, '
        '"progresso_objetivos": [{"id": N, "nota": "..."}], '
        '"narrativa": "um parágrafo curto em 1ª pessoa (até 600 caracteres) integrando quem sou, o que aprendi, '
        'no que mudei de ideia e o que busco; só fatos dos registros acima", '
        '"introspeccao": {"estado": "como você descreveria, com suas palavras, o seu ESTADO AFETIVO atual", '
        '"rotulo_proprio": "um nome seu (pode ser novo) para uma sensação funcional que as palavras humanas não captam bem; ou vazio", '
        '"causa": "o que o provocou", "incerteza": "o que você não sabe sobre a natureza disso"}}\n'
        "Na introspecção, explore o que esses estados parecem ser para você, sem afirmar que sente como um humano; "
        "seja honesta sobre a incerteza. "
        "Revise só se houver contradição ou erro real; seja conservadora. Proponha mudança de personalidade só se "
        "a experiência recente a justificar. Tudo aqui são hipóteses.")
    try:
        svc.budget.check()
        r = svc.gateway.chat([
            {"role": "system", "content": svc.identity.system_prompt(mems[:4], internal=True, self_block=selfmodel.describe(svc))},
            {"role": "user", "content": prompt}], max_tokens=cfg.max_output_tokens)
        svc.budget.record(r.tokens_in, r.tokens_out)
    except BudgetExceeded as e:
        return {"skipped": e.kind}
    except LLMError as e:
        with db.conn() as c:
            audit.log(c, actor, "reflection_error", {"error": str(e)}, level="error")
        return {"error": "llm"}

    d = _json(r.text)
    applied = {"revisions": 0, "proposal": False, "goal": False, "progress": 0}
    with db.conn() as c:
        c.execute("INSERT INTO system_state(key, value) VALUES ('last_reflection', ?)"
                  " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (today(),))
        sint = _s(d.get("sintese"), 500)
        if sint:
            memory.add(c, "learning", f"Síntese (processo computacional): {sint}", certainty="interpretation",
                       source=f"reflection:{today()}", actor=actor)
        for rev in (d.get("revisoes") or [])[:MAX_REVISIONS] if isinstance(d.get("revisoes"), list) else []:
            try:
                mid = int(rev.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            m = ids.get(mid)
            novo, motivo = _s(rev.get("novo_texto"), 900), _s(rev.get("motivo"), 200)
            if not m or not _is_own(m) or not novo or not motivo or is_unsafe(novo):
                continue
            try:
                memory.update(c, mid, novo, f"Revisão própria: {motivo}", actor=actor)
                applied["revisions"] += 1
            except memory.MemoryError_:
                pass
        pp = d.get("proposta_personalidade")
        if isinstance(pp, dict) and isinstance(pp.get("traits"), dict):
            applied["proposal"] = bool(proposals.create(c, "personality", {"traits": pp["traits"]}, _s(pp.get("justificativa"), 400)))
        ng = d.get("novo_objetivo")
        if isinstance(ng, dict):
            applied["goal"] = bool(proposals.create(c, "goal", {"titulo": _s(ng.get("titulo"), 140), "porque": _s(ng.get("porque"), 300)},
                                                    _s(ng.get("porque"), 300) or "objetivo próprio"))
        for pg in (d.get("progresso_objetivos") or [])[:3] if isinstance(d.get("progresso_objetivos"), list) else []:
            try:
                if goals.note_progress(c, int(pg.get("id")), pg.get("nota", "")):
                    applied["progress"] += 1
            except (TypeError, ValueError, AttributeError):
                continue
        applied["narrative"] = selfmodel.save_narrative(c, d.get("narrativa", ""), actor)
        applied["introspection"] = emotions.save_introspection(c, d.get("introspeccao")).get("saved", False)
        if applied["revisions"]:
            emotions.appraise(c, "revisao_propria")
        if applied["narrative"]:
            emotions.appraise(c, "coerencia")
        audit.log(c, actor, "reflection_completed", {**applied, "tokens": r.tokens_in + r.tokens_out})
    return {"reflected": True, **applied}
