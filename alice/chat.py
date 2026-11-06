"""Orquestra uma conversa: pausa, orçamento, memória relevante, chamada ao LLM, registro e auditoria."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from . import audit, emotions, memory, profiles, selfmodel, style
from .budget import Budget, BudgetExceeded
from .curiosity import is_unsafe
from .llm_gateway import LLMError
from .util import now

MEM_TAG = re.compile(r"\[\[MEMORIA:\s*(\w+)\s*\|\s*(.+?)\s*\]\]", re.S)
SELF_CATEGORIES = {"learning", "reflection", "visitor"}
THOUGHT = re.compile(r"<pensamento>(.*?)</pensamento>", re.S | re.I)


def split_thought(text: str) -> tuple[str, str]:
    """Separa o raciocínio interno da resposta. Pensamento nunca vai para o visitante."""
    m = THOUGHT.search(text)
    thought = m.group(1).strip()[:500] if m else ""
    text = THOUGHT.sub("", text)
    low = text.lower()
    if "<pensamento>" in low:  # pensamento não fechado (resposta cortada): descarta dali em diante
        text = text[: low.index("<pensamento>")]
    return thought, re.sub(r"</?pensamento>", "", text, flags=re.I).strip()


class ServicePaused(Exception):
    pass


class InvalidInput(ValueError):
    pass


class AliceService:
    def __init__(self, db, identity, gateway, budget: Budget, cfg):
        self.db, self.identity, self.gateway, self.budget, self.cfg = db, identity, gateway, budget, cfg

    # ---- pausa / desligamento (Art. 33) -----------------------------------
    def is_paused(self) -> bool:
        with self.db.conn() as c:
            r = c.execute("SELECT value FROM system_state WHERE key = 'paused'").fetchone()
        return bool(r and r["value"] == "1")

    def set_paused(self, paused: bool, actor="admin") -> None:
        with self.db.conn() as c:
            c.execute("INSERT INTO system_state(key, value) VALUES ('paused', ?)"
                      " ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("1" if paused else "0",))
            audit.log(c, actor, "paused" if paused else "resumed")
            emotions.appraise(c, "pausa" if paused else "retomada")

    # ---- visitantes (Art. 29, 30) -------------------------------------------
    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_session(self, name: str | None, consent: bool) -> dict:
        """Sem consentimento nada é gravado: a conversa fica só no navegador do visitante."""
        if not consent:
            return {"token": None, "stored": False}
        name = (name or "").strip()[:40] or None
        token = secrets.token_urlsafe(32)
        with self.db.conn() as c:
            c.execute("INSERT INTO visitors(token_hash, display_name, created_at, last_seen) VALUES (?,?,?,?)",
                      (self._hash(token), name, now(), now()))
            audit.log(c, "visitor", "session_created", {"named": bool(name)})
        return {"token": token, "stored": True}

    def _visitor(self, c, token: str | None):
        if not token:
            return None
        return c.execute("SELECT * FROM visitors WHERE token_hash = ?", (self._hash(token),)).fetchone()

    def delete_visitor(self, token: str) -> bool:
        with self.db.conn() as c:
            v = self._visitor(c, token)
            if not v:
                return False
            c.execute("DELETE FROM visitors WHERE id = ?", (v["id"],))  # cascata: mensagens e memórias dele
            audit.log(c, "visitor", "visitor_data_deleted")
        return True

    def purge_inactive(self, days: int) -> int:
        """Retenção (Art. 30): apaga visitantes sem atividade há `days` dias, com mensagens e memórias."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.db.conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM visitors WHERE last_seen < ?", (cutoff,))]
            for i in ids:
                c.execute("DELETE FROM visitors WHERE id = ?", (i,))
            if ids:
                audit.log(c, "system", "retention_purge", {"visitors": len(ids), "days": days})
        return len(ids)

    def session_info(self, token: str) -> dict | None:
        """Para reconhecer quem volta (com consentimento): nome e assuntos em aberto."""
        with self.db.conn() as c:
            v = self._visitor(c, token)
            if not v:
                return None
            p = profiles.get(c, v["id"])
            n = c.execute("SELECT COUNT(*) FROM messages WHERE visitor_id = ?", (v["id"],)).fetchone()[0]
            return {"name": v["display_name"], "messages": n, "open_threads": (p or {}).get("open_threads")}

    def visitor_profile(self, token: str) -> dict | None:
        with self.db.conn() as c:
            v = self._visitor(c, token)
            if not v:
                return None
            return profiles.get(c, v["id"]) or {"summary": None, "open_threads": None, "updated_at": None}

    def clear_profile(self, token: str) -> bool:
        with self.db.conn() as c:
            v = self._visitor(c, token)
            return profiles.clear(c, v["id"]) if v else False

    def purge_thoughts(self, days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.db.conn() as c:
            return c.execute("DELETE FROM thoughts WHERE created_at < ?", (cutoff,)).rowcount

    def visitor_export(self, token: str) -> dict | None:
        with self.db.conn() as c:
            v = self._visitor(c, token)
            return memory.export_visitor(c, v["id"]) if v else None

    def visitor_memories(self, token: str) -> list[dict] | None:
        with self.db.conn() as c:
            v = self._visitor(c, token)
            if not v:
                return None
            return [dict(r) for r in c.execute(
                "SELECT id, content, certainty, created_at FROM memories WHERE visitor_id = ? AND deleted_at IS NULL", (v["id"],))]

    def delete_visitor_memory(self, token: str, memory_id: int) -> bool:
        with self.db.conn() as c:
            v = self._visitor(c, token)
            if not v:
                return False
            n = c.execute("DELETE FROM memories WHERE id = ? AND visitor_id = ?", (memory_id, v["id"])).rowcount
            if n:
                audit.log(c, "visitor", "visitor_memory_deleted", {"id": memory_id})
            return bool(n)

    # ---- conversa ---------------------------------------------------------------
    def chat(self, token: str | None, message: str, history: list[dict] | None = None) -> dict:
        message = (message or "").strip()
        if not message:
            raise InvalidInput("mensagem vazia")
        if len(message) > self.cfg.max_message_chars:
            raise InvalidInput(f"mensagem acima de {self.cfg.max_message_chars} caracteres")
        if self.is_paused():
            raise ServicePaused()

        qvec = memory._embed(message)  # fora de transação (provedor remoto, se houver, pode demorar)
        with self.db.conn() as c:
            visitor = self._visitor(c, token)
            if token and not visitor:
                raise InvalidInput("sessão inválida ou removida")
            vid = visitor["id"] if visitor else None
            mems = memory.search(c, message, visitor_id=vid, qvec=qvec)
            memory.touch(c, [m["id"] for m in mems])  # reforço: o que é lembrado se consolida
            if visitor:
                c.execute("UPDATE visitors SET last_seen = ? WHERE id = ?", (now(), vid))
                rows = c.execute("SELECT role, content FROM messages WHERE visitor_id = ?"
                                 " ORDER BY id DESC LIMIT ?", (vid, self.cfg.history_turns * 2)).fetchall()
                hist = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
            else:  # sem consentimento: histórico vem do cliente, limitado e sanitizado
                hist = [{"role": h["role"], "content": str(h["content"])[: self.cfg.max_message_chars]}
                        for h in (history or [])[-self.cfg.history_turns * 2:]
                        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and "content" in h]
            name = visitor["display_name"] if visitor else None
            vblock = profiles.block(c, vid) if vid else ""
        prev_user = [h["content"] for h in hist if h["role"] == "user"]

        self.budget.check()  # pode levantar BudgetExceeded
        messages = [{"role": "system", "content": self.identity.system_prompt(
            mems, name, self_block=selfmodel.describe(self), deliberate=self.cfg.deliberate,
            visitor_block=vblock, style_block=style.hint(message, prev_user))}] \
            + hist + [{"role": "user", "content": message}]
        try:
            result = self.gateway.chat(messages, max_tokens=self.cfg.max_output_tokens)
        except LLMError as e:
            with self.db.conn() as c:
                audit.log(c, "system", "llm_error", {"error": str(e)}, level="error")
                emotions.appraise(c, "falha_tecnica")
            raise
        self.budget.record(result.tokens_in, result.tokens_out)

        thought, text = split_thought(result.text)
        reply, saved = self._extract_memories(text, vid)
        if not reply:
            reply = "Desculpe, não consegui formular uma resposta agora. Pode reformular?"
        with self.db.conn() as c:
            if visitor:
                for role, content in (("user", message), ("assistant", reply)):
                    c.execute("INSERT INTO messages(visitor_id, role, content, created_at) VALUES (?,?,?,?)",
                              (vid, role, content, now()))
            if visitor and thought:  # raciocínio só é guardado com consentimento; apagado em ~7 dias
                c.execute("INSERT INTO thoughts(visitor_id, content, created_at) VALUES (?,?,?)", (vid, thought, now()))
            # Auditoria registra metadados, nunca o conteúdo da conversa.
            audit.log(c, "alice", "chat", {"stored": bool(visitor), "model": result.model,
                                           "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                                           "memories_saved": saved})
            emotions.appraise(c, "conversa")
            ev = emotions.message_event(message)  # só o rótulo do evento é usado; o texto não é guardado
            if ev:
                emotions.appraise(c, ev)
        if visitor:
            try:  # notas de convívio: nunca derrubam a conversa
                profiles.tick(self, vid)
            except Exception:  # noqa: BLE001
                pass
        return {"reply": reply, "stored": bool(visitor), "memories_saved": saved}

    def _extract_memories(self, text: str, visitor_id: int | None) -> tuple[str, int]:
        saved = 0
        with self.db.conn() as c:
            for cat, content in MEM_TAG.findall(text)[:2]:
                cat = cat.lower()
                if is_unsafe(content):  # visitante tentando "plantar" instruções na memória da Alice
                    audit.log(c, "alice", "memory_tag_rejected", {"category": cat, "reason": "unsafe"}, level="warning")
                    continue
                if cat not in SELF_CATEGORIES or (cat == "visitor" and visitor_id is None):
                    audit.log(c, "alice", "memory_tag_rejected", {"category": cat}, level="warning")
                    continue
                try:  # o que a Alice registra sozinha é sempre marcado como interpretação, não como fato verificado
                    memory.add(c, cat, content, certainty="interpretation", source="self",
                               visitor_id=visitor_id if cat == "visitor" else None, actor="alice")
                    saved += 1
                except memory.MemoryError_:
                    pass
        return MEM_TAG.sub("", text).strip(), saved
