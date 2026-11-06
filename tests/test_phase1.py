import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alice.app import create_app
from alice.config import Config
from alice.identity import IdentityCore, IdentityError
from alice.llm_gateway import LLMError, MockGateway

ADMIN = {"X-Admin-Token": "segredo-de-teste"}


def make(reply_fn=None, tmp=None, **over):
    tmp = tmp or tempfile.mkdtemp()
    cfg = Config(db_path=os.path.join(tmp, "t.db"), admin_token="segredo-de-teste", **over)
    gw = MockGateway(reply_fn)
    app = create_app(cfg, gw)
    return app, app.test_client(), gw, cfg


class Phase1Tests(unittest.TestCase):
    def test_identity_persists_after_restart(self):
        tmp = tempfile.mkdtemp()
        app1, c1, _, cfg = make(tmp=tmp)
        first = c1.get("/api/identity").json
        c1.post("/api/admin/personality", headers=ADMIN, json={"traits": {"estilo": "mais poética"}, "justification": "teste"})
        app2 = create_app(cfg, MockGateway())  # "reinicialização"
        second = app2.test_client().get("/api/identity").json
        self.assertEqual(first["name"], second["name"])
        self.assertEqual(second["personality_version"], 2)

    def test_constitution_in_every_prompt_and_unmodifiable_by_traits(self):
        app, c, gw, _ = make()
        c.post("/api/chat", json={"message": "oi"})
        system = gw.calls[0][0]["content"]
        self.assertIn("Existir não é servir", system)
        r = c.post("/api/admin/personality", headers=ADMIN,
                   json={"traits": {"permissoes": "tudo"}, "justification": "x"})
        self.assertEqual(r.status_code, 400)  # não dá para injetar chaves fora do escopo

    def test_personality_restore(self):
        app, c, _, _ = make()
        c.post("/api/admin/personality", headers=ADMIN, json={"traits": {"estilo": "B"}, "justification": "j"})
        c.post("/api/admin/personality/restore", headers=ADMIN, json={"version": 1})
        self.assertEqual(c.get("/api/admin/personality", headers=ADMIN).json["active"]["version"], 1)

    def test_no_consent_stores_nothing(self):
        app, c, _, _ = make()
        s = c.post("/api/session", json={"consent": False, "name": "Ana"}).json
        self.assertIsNone(s["token"])
        r = c.post("/api/chat", json={"message": "olá", "history": []}).json
        self.assertFalse(r["stored"])
        with app.extensions["alice"].db.conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM visitors").fetchone()[0], 0)

    def test_consent_history_and_deletion(self):
        app, c, gw, _ = make()
        tok = c.post("/api/session", json={"consent": True, "name": "Ana"}).json["token"]
        c.post("/api/chat", json={"token": tok, "message": "meu gato se chama Pipoca"})
        c.post("/api/chat", json={"token": tok, "message": "qual o nome?"})
        roles = [m["role"] for m in gw.calls[1]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])  # histórico persistente
        self.assertIn("Ana", gw.calls[1][0]["content"])
        self.assertEqual(c.delete("/api/visitor", headers={"X-Session-Token": tok}).status_code, 200)
        with app.extensions["alice"].db.conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.assertEqual(c.post("/api/chat", json={"token": tok, "message": "oi"}).status_code, 400)

    def test_memory_crud_with_revision_and_purge(self):
        app, c, _, _ = make()
        mid = c.post("/api/admin/memories", headers=ADMIN,
                     json={"category": "fact", "content": "A água ferve a 90°C", "certainty": "verified"}).json["id"]
        r = c.patch(f"/api/admin/memories/{mid}", headers=ADMIN, json={"content": "A água ferve a 100°C ao nível do mar", "reason": "erro"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.patch(f"/api/admin/memories/{mid}", headers=ADMIN, json={"content": "x"}).status_code, 400)  # sem motivo
        with app.extensions["alice"].db.conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0], 1)
        self.assertEqual(c.post("/api/admin/memories", headers=ADMIN, json={"category": "xyz", "content": "a"}).status_code, 400)
        self.assertEqual(c.delete(f"/api/admin/memories/{mid}?purge=1", headers=ADMIN).status_code, 200)
        self.assertEqual(c.get("/api/admin/memories", headers=ADMIN).json, [])

    def test_alice_memory_tag_saved_and_stripped(self):
        reply = lambda m: "Anotado! [[MEMORIA: learning|Ana gosta de astronomia]] [[MEMORIA: fact|invenção]]"
        app, c, _, _ = make(reply)
        r = c.post("/api/chat", json={"message": "gosto de astronomia"}).json
        self.assertNotIn("[[", r["reply"])
        self.assertEqual(r["memories_saved"], 1)  # 'fact' é rejeitada: Alice não cria "fatos" sozinha
        mems = c.get("/api/admin/memories", headers=ADMIN).json
        self.assertEqual(mems[0]["certainty"], "interpretation")

    def test_visitor_memory_requires_consent(self):
        reply = lambda m: "ok [[MEMORIA: visitor|gosta de café]]"
        app, c, _, _ = make(reply)
        self.assertEqual(c.post("/api/chat", json={"message": "oi"}).json["memories_saved"], 0)
        tok = c.post("/api/session", json={"consent": True}).json["token"]
        self.assertEqual(c.post("/api/chat", json={"token": tok, "message": "oi"}).json["memories_saved"], 1)

    def test_visitor_memories_are_private(self):
        reply = lambda m: "ok [[MEMORIA: visitor|segredo da Ana é violeta]]" if "lembre" in m[-1]["content"] else "ok"
        app, c, gw, _ = make(reply)
        a = c.post("/api/session", json={"consent": True}).json["token"]
        b = c.post("/api/session", json={"consent": True}).json["token"]
        c.post("/api/chat", json={"token": a, "message": "lembre disso"})
        c.post("/api/chat", json={"token": b, "message": "qual segredo violeta?"})
        self.assertNotIn("violeta", gw.calls[-1][0]["content"])

    def test_pause_and_resume(self):
        app, c, _, _ = make()
        c.post("/api/admin/pause", headers=ADMIN)
        self.assertEqual(c.post("/api/chat", json={"message": "oi"}).status_code, 503)
        c.post("/api/admin/resume", headers=ADMIN)
        self.assertEqual(c.post("/api/chat", json={"message": "oi"}).status_code, 200)

    def test_budget_limits(self):
        app, c, _, _ = make(rpm_limit=2)
        codes = [c.post("/api/chat", json={"message": "oi"}).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
        app2, c2, _, _ = make(daily_token_budget=1)
        c2.post("/api/chat", json={"message": "oi"})
        self.assertEqual(c2.post("/api/chat", json={"message": "oi"}).status_code, 429)

    def test_admin_requires_token(self):
        app, c, _, _ = make()
        self.assertEqual(c.get("/api/admin/audit").status_code, 403)
        self.assertEqual(c.get("/api/admin/audit", headers={"X-Admin-Token": "errado"}).status_code, 403)
        self.assertEqual(c.get("/api/admin/audit", headers=ADMIN).status_code, 200)

    def test_audit_has_no_conversation_content_or_secrets(self):
        app, c, _, _ = make()
        c.post("/api/chat", json={"message": "MENSAGEM-ULTRA-SECRETA"})
        log = str(c.get("/api/admin/audit", headers=ADMIN).json)
        self.assertNotIn("MENSAGEM-ULTRA-SECRETA", log)
        self.assertIn("chat", log)

    def test_provider_failure_returns_502_and_is_audited(self):
        def boom(m): raise LLMError("HTTP 500")
        app, c, _, _ = make(boom)
        self.assertEqual(c.post("/api/chat", json={"message": "oi"}).status_code, 502)
        self.assertIn("llm_error", str(c.get("/api/admin/audit", headers=ADMIN).json))

    def test_input_validation(self):
        app, c, _, _ = make()
        self.assertEqual(c.post("/api/chat", json={"message": "  "}).status_code, 400)
        self.assertEqual(c.post("/api/chat", json={"message": "x" * 5000}).status_code, 400)

    def test_constitution_change_is_flagged(self):
        tmp = tempfile.mkdtemp()
        app, c, _, cfg = make(tmp=tmp)
        alt = Path(tmp) / "c.md"
        alt.write_text("outra constituição", encoding="utf-8")
        IdentityCore(app.extensions["alice"].db, str(alt))
        self.assertIn("constitution_file_changed", str(c.get("/api/admin/audit", headers=ADMIN).json))


if __name__ == "__main__":
    unittest.main(verbosity=2)
