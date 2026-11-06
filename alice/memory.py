"""Memória viva: persistente, associativa e com esquecimento natural.
- Busca híbrida: BM25 (palavras) + vetores (semelhança) + ativação por associação (memórias ligadas).
- Importância: memórias criadas sob emoção forte duram mais (Art. 52).
- Esquecimento natural (Art. 51): memórias próprias, pouco usadas e antigas são ARQUIVADAS (nunca apagadas);
  verificadas e fixadas pelo admin não esmaecem. Tudo é restaurável."""
import math
from datetime import datetime, timezone

from . import audit, emotions, embeddings
from . import search as _search
from .util import now

CATEGORIES = ("fact", "event", "learning", "visitor", "reflection", "external")
CERTAINTIES = ("verified", "hypothesis", "interpretation", "memory", "unknown")
MAX_CONTENT = 1000
MAX_VISITOR_MEMORIES = 20  # memória social limitada (Fase 4)
SEM_MIN = 0.28             # similaridade mínima para entrar só pelo vetor
FORGET_BELOW = 0.05
LINK_MIN = 0.45
BASE_IMPORTANCE = {"fact": 0.8, "visitor": 0.7, "learning": 0.6, "reflection": 0.5, "external": 0.5, "event": 0.4}

EMBEDDER = embeddings.HashEmbedder()


def set_embedder(e) -> None:
    global EMBEDDER
    EMBEDDER = e


class MemoryError_(ValueError):
    pass


def _validate(category=None, certainty=None, content=None):
    if category is not None and category not in CATEGORIES:
        raise MemoryError_(f"categoria inválida; use uma de {CATEGORIES}")
    if certainty is not None and certainty not in CERTAINTIES:
        raise MemoryError_(f"certainty inválido; use um de {CERTAINTIES}")
    if content is not None and not (1 <= len(content.strip()) <= MAX_CONTENT):
        raise MemoryError_(f"conteúdo deve ter entre 1 e {MAX_CONTENT} caracteres")


def _embed(text: str) -> list[float] | None:
    try:
        return EMBEDDER.embed(text)
    except Exception:  # noqa: BLE001 — provedor remoto fora do ar: a memória segue sem vetor
        return None


def _dt(iso: str | None) -> datetime:
    try:
        d = datetime.fromisoformat(iso) if iso else None
    except ValueError:
        d = None
    return d if d and d.tzinfo else (d.replace(tzinfo=timezone.utc) if d else datetime.now(timezone.utc))


def strength(r: dict, t: datetime | None = None) -> float:
    """Curva de esquecimento: cai com o tempo sem uso; uso e importância aumentam a estabilidade."""
    if r.get("pinned"):
        return 1.0
    t = t or datetime.now(timezone.utc)
    days = max(0.0, (t - _dt(r.get("last_used_at") or r["created_at"])).total_seconds() / 86400)
    stability = 7.0 * (1 + min(r.get("uses", 0), 10)) * (0.5 + r.get("importance", 0.5))
    return math.exp(-days / stability)


def add(c, category, content, certainty="memory", source=None, visitor_id=None, actor="system") -> int:
    _validate(category, certainty, content)
    if category == "visitor" and visitor_id is None:
        raise MemoryError_("memória de visitante exige visitor_id (e consentimento)")
    if category == "visitor":
        n = c.execute("SELECT COUNT(*) FROM memories WHERE visitor_id = ? AND deleted_at IS NULL", (visitor_id,)).fetchone()[0]
        if n >= MAX_VISITOR_MEMORIES:
            raise MemoryError_("limite de memórias por visitante atingido")
    st = emotions.get_state(c)
    salience = max(abs(st["valence"] - emotions.BASE["valence"]), st["arousal"] - emotions.BASE["arousal"], 0.0)
    importance = min(1.0, BASE_IMPORTANCE.get(category, 0.5) + (0.2 if salience >= 0.3 else 0.0))
    pinned = 1 if (certainty == "verified" or actor == "admin") else 0
    vec = _embed(content)
    ts = now()
    cur = c.execute(
        "INSERT INTO memories(category, certainty, content, source, visitor_id, created_at, updated_at,"
        " importance, pinned, emb, emb_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (category, certainty, content.strip(), source, visitor_id, ts, ts, importance, pinned,
         embeddings.quantize(vec) if vec else None, EMBEDDER.name if vec else None))
    mid = cur.lastrowid
    if vec and visitor_id is None:  # associações entre memórias próprias semelhantes
        for r in c.execute("SELECT id, emb FROM memories WHERE id != ? AND deleted_at IS NULL AND archived_at IS NULL"
                           " AND visitor_id IS NULL AND emb_name = ? ORDER BY id DESC LIMIT 300", (mid, EMBEDDER.name)):
            v = embeddings.dequantize(r["emb"])
            sim = embeddings.cosine(vec, v) if v else 0.0
            if sim >= LINK_MIN:
                c.execute("INSERT OR REPLACE INTO memory_links(a, b, weight) VALUES (?,?,?)", (min(mid, r["id"]), max(mid, r["id"]), round(sim, 3)))
    audit.log(c, actor, "memory_added", {"id": mid, "category": category, "certainty": certainty})
    return mid


def get(c, memory_id: int) -> dict | None:
    r = c.execute("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (memory_id,)).fetchone()
    return dict(r) if r else None


def list_(c, category=None, limit=100, archived=False) -> list[dict]:
    q, args = "SELECT * FROM memories WHERE deleted_at IS NULL AND archived_at IS " + ("NOT NULL" if archived else "NULL"), []
    if category:
        _validate(category=category)
        q += " AND category = ?"
        args.append(category)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(min(limit, 500))
    rows = [dict(r) for r in c.execute(q, args).fetchall()]
    for r in rows:
        r.pop("emb", None)  # vetor é detalhe interno
        r["strength"] = round(strength(r), 3)
    return rows


def update(c, memory_id, new_content, reason, certainty=None, actor="admin") -> None:
    _validate(certainty=certainty, content=new_content)
    if not reason or not reason.strip():
        raise MemoryError_("informe o motivo da correção")
    old = get(c, memory_id)
    if not old:
        raise MemoryError_("memória não encontrada")
    c.execute(
        "INSERT INTO memory_revisions(memory_id, old_content, new_content, reason, created_at) VALUES (?,?,?,?,?)",
        (memory_id, old["content"], new_content.strip(), reason.strip(), now()),
    )
    vec = _embed(new_content)
    c.execute("UPDATE memories SET content = ?, certainty = COALESCE(?, certainty), updated_at = ?, emb = ?, emb_name = ? WHERE id = ?",
              (new_content.strip(), certainty, now(), embeddings.quantize(vec) if vec else None, EMBEDDER.name if vec else None, memory_id))
    audit.log(c, actor, "memory_corrected", {"id": memory_id})


def delete(c, memory_id: int, purge: bool = False, actor="admin") -> bool:
    if purge:
        c.execute("DELETE FROM memory_revisions WHERE memory_id = ?", (memory_id,))
        c.execute("DELETE FROM memory_links WHERE a = ? OR b = ?", (memory_id, memory_id))
        n = c.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount
    else:
        n = c.execute("UPDATE memories SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                      (now(), memory_id)).rowcount
    if n:
        audit.log(c, actor, "memory_purged" if purge else "memory_deleted", {"id": memory_id})
    return bool(n)


def revisions(c, memory_id: int) -> list[dict]:
    return [dict(r) for r in c.execute(
        "SELECT * FROM memory_revisions WHERE memory_id = ? ORDER BY id", (memory_id,)).fetchall()]


# ---------- esquecimento natural ----------
def consolidate(c, actor="system") -> int:
    """Arquiva (não apaga) memórias próprias que esmaeceram. Verificadas e fixadas nunca esmaecem."""
    t, n = datetime.now(timezone.utc), 0
    for r in c.execute("SELECT * FROM memories WHERE deleted_at IS NULL AND archived_at IS NULL AND pinned = 0"
                       " AND visitor_id IS NULL").fetchall():
        if strength(dict(r), t) < FORGET_BELOW:
            c.execute("UPDATE memories SET archived_at = ? WHERE id = ?", (now(), r["id"]))
            n += 1
    if n:
        audit.log(c, actor, "memories_archived", {"count": n})
    return n


def restore(c, memory_id: int, actor="admin") -> bool:
    n = c.execute("UPDATE memories SET archived_at = NULL, last_used_at = ? WHERE id = ? AND archived_at IS NOT NULL",
                  (now(), memory_id)).rowcount
    if n:
        audit.log(c, actor, "memory_restored", {"id": memory_id})
    return bool(n)


def pin(c, memory_id: int, pinned: bool, actor="admin") -> bool:
    n = c.execute("UPDATE memories SET pinned = ? WHERE id = ? AND deleted_at IS NULL", (1 if pinned else 0, memory_id)).rowcount
    if n:
        audit.log(c, actor, "memory_pinned" if pinned else "memory_unpinned", {"id": memory_id})
    return bool(n)


# ---------- busca híbrida ----------
def search(c, text: str, visitor_id: int | None = None, limit: int = 6, qvec: list[float] | None = None) -> list[dict]:
    """BM25 + similaridade vetorial + associações, ponderados pela força da memória. Visitantes só veem as próprias."""
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM memories WHERE deleted_at IS NULL AND archived_at IS NULL AND (visitor_id IS NULL OR visitor_id = ?)"
        " ORDER BY updated_at DESC LIMIT 500", (visitor_id if visitor_id else -1,)).fetchall()]
    if not rows:
        return []
    qv = qvec or _embed(text)
    bm = _search.bm25(text, [r["content"] for r in rows])
    mx, t, lazy = (max(bm) or 0.0), datetime.now(timezone.utc), 0
    scored: dict[int, float] = {}
    for r, b in zip(rows, bm):
        v = embeddings.dequantize(r["emb"]) if r.get("emb") and r.get("emb_name") == EMBEDDER.name else None
        if v is None and lazy < 30:  # vetores de memórias antigas são calculados sob demanda
            lazy += 1
            nv = _embed(r["content"])
            if nv:
                c.execute("UPDATE memories SET emb = ?, emb_name = ? WHERE id = ?", (embeddings.quantize(nv), EMBEDDER.name, r["id"]))
                v = nv
        sem = max(0.0, embeddings.cosine(qv, v)) if (qv and v) else 0.0
        bn = b / mx if mx > 0 else 0.0
        if bn <= 0 and sem < SEM_MIN:
            continue
        scored[r["id"]] = (0.55 * bn + 0.45 * sem) * (0.4 + 0.6 * strength(r, t)) * (1 + 0.1 * min(r["uses"], 5))
    by_id = {r["id"]: r for r in rows}
    top = sorted(scored, key=lambda i: -scored[i])[:2]
    if top:  # ativação por associação: memórias ligadas às melhores ganham um empurrão
        q = ",".join("?" * len(top))
        for l in c.execute(f"SELECT a, b, weight FROM memory_links WHERE a IN ({q}) OR b IN ({q})", top + top).fetchall():
            other = l["b"] if l["a"] in top else l["a"]
            if other in by_id:
                scored[other] = scored.get(other, 0.0) + 0.15 * l["weight"]
    ids = sorted(scored, key=lambda i: -scored[i])[:limit]
    out = [by_id[i] for i in ids]
    if visitor_id:  # sempre inclui as lembranças mais recentes do próprio visitante
        out += [r for r in rows if r["visitor_id"] == visitor_id and r not in out][:2]
    for r in out:
        r.pop("emb", None)
    return out


def touch(c, ids: list[int]) -> None:
    for i in ids:
        c.execute("UPDATE memories SET uses = uses + 1, last_used_at = ? WHERE id = ?", (now(), i))


def export_visitor(c, visitor_id: int) -> dict:
    v = c.execute("SELECT display_name, created_at, last_seen FROM visitors WHERE id = ?", (visitor_id,)).fetchone()
    msgs = c.execute("SELECT role, content, created_at FROM messages WHERE visitor_id = ? ORDER BY id", (visitor_id,)).fetchall()
    mems = c.execute("SELECT id, content, certainty, created_at FROM memories WHERE visitor_id = ? AND deleted_at IS NULL", (visitor_id,)).fetchall()
    prof = c.execute("SELECT summary, open_threads, updated_at FROM visitor_profiles WHERE visitor_id = ?", (visitor_id,)).fetchone()
    pm = c.execute("SELECT kind, text, created_at, delivered_at FROM proactive_messages WHERE visitor_id = ? ORDER BY id", (visitor_id,)).fetchall()
    return {"visitor": dict(v), "messages": [dict(m) for m in msgs], "memories": [dict(m) for m in mems],
            "profile": dict(prof) if prof else None, "proactive_messages": [dict(m) for m in pm]}
