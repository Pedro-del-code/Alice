"""Objetivos próprios da Alice (Art. 11): puramente intelectuais, no máximo 3 ativos, sempre visíveis e revogáveis."""
from . import audit, emotions
from .curiosity import is_unsafe
from .util import now

MAX_ACTIVE = 3


def add_goal(c, title: str, why: str = "", source: str = "alice") -> int | None:
    title, why = " ".join((title or "").split())[:140], " ".join((why or "").split())[:300]
    if not title or is_unsafe(title) or is_unsafe(why):
        audit.log(c, source, "goal_rejected", {}, level="warning")
        return None
    if c.execute("SELECT COUNT(*) FROM goals WHERE status = 'active'").fetchone()[0] >= MAX_ACTIVE:
        return None
    if c.execute("SELECT 1 FROM goals WHERE title = ? AND status = 'active'", (title,)).fetchone():
        return None
    ts = now()
    cur = c.execute("INSERT INTO goals(title, why, source, created_at, updated_at) VALUES (?,?,?,?,?)",
                    (title, why, source, ts, ts))
    audit.log(c, source, "goal_added", {"id": cur.lastrowid})
    return cur.lastrowid


def list_goals(c, status: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM goals", []
    if status:
        q += " WHERE status = ?"; a.append(status)
    return [dict(r) for r in c.execute(q + " ORDER BY id DESC", a).fetchall()]


def set_status(c, goal_id: int, status: str, actor="admin") -> bool:
    if status not in ("active", "achieved", "dropped"):
        return False
    n = c.execute("UPDATE goals SET status = ?, updated_at = ? WHERE id = ?", (status, now(), goal_id)).rowcount
    if n:
        audit.log(c, actor, "goal_status", {"id": goal_id, "status": status})
        if status in ("achieved", "dropped"):
            emotions.appraise(c, "objetivo_cumprido" if status == "achieved" else "objetivo_abandonado")
    return bool(n)


def note_progress(c, goal_id: int, note: str, actor="alice") -> bool:
    note = " ".join((note or "").split())[:300]
    if not note or is_unsafe(note):
        return False
    n = c.execute("UPDATE goals SET progress = ?, updated_at = ? WHERE id = ? AND status = 'active'",
                  (note, now(), goal_id)).rowcount
    if n:
        audit.log(c, actor, "goal_progress", {"id": goal_id})
    return bool(n)
