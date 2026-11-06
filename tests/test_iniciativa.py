import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))

from alice import emotions, outreach
from alice.app import create_app
from alice.config import Config
from alice.llm_gateway import MockGateway
from test_pensamento import smart

ADMIN = {"X-Admin-Token": "segredo-de-teste"}
ROOT = HERE.parent
THREAD = "escolher um violão"


def make(reply_fn=None, **over):
    tmp = tempfile.mkdtemp()
    cfg = Config(db_path=os.path.join(tmp, "t.db"), admin_token="segredo-de-teste", **over)
    gw = MockGateway(reply_fn)
    app = create_app(cfg, gw)
    set_hour(12)  # meio-dia: fora do horário de descanso
    return app, app.test_client(), gw, cfg


def set_hour(h):
    utc = datetime.now(timezone.utc).hour
    emotions.TZ_OFFSET = ((h - utc + 12) % 24) - 12


def iso(seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def outreach_reply(text="Ei, pensei no violão que você ia escolher. Já decidiu?"):
    def fn(msgs):
        if "Motivo para falar agora" in msgs[-1]["content"]:
            return json.dumps({"mensagem": text})
        return "ok"
    return fn


def visitor(app, c, proactive=True, thread=THREAD, summary="Pedro gosta de música e está aprendendo violão."):
    tok = c.post("/api/session", json={"consent": True, "name": "Pedro", "proactive": proactive}).json["token"]
    with app.extensions["alice"].db.conn() as conn:
        vid = conn.execute("SELECT id FROM visitors").fetchall()[-1]["id"]
        if thread or summary:
            conn.execute("INSERT OR REPLACE INTO visitor_profiles(visitor_id, summary, open_threads, msgs_since, updated_at) VALUES (?,?,?,0,?)",
                         (vid, summary, thread, iso()))
    return tok, vid


def count(app, sql, *a):
    with app.extensions["alice"].db.conn() as conn:
        return conn.execute(sql, a).fetchone()[0]


class PersonaCompacta(unittest.TestCase):
    def test_compact_is_default_shorter_and_keeps_the_principle(self):
        app, c, gw, _ = make()
        c.post("/api/chat", json={"message": "oi"})
        compact = gw.calls[0][0]["content"]
        app2, c2, gw2, _ = make(prompt_mode="full")
        c2.post("/api/chat", json={"message": "oi"})
        full = gw2.calls[0][0]["content"]
        self.assertIn("QUEM EU SOU E MEUS LIMITES", compact); self.assertNotIn("=== CONSTITUIÇÃO (camada superior", compact)
        self.assertIn("Existir não é servir", compact)
        self.assertIn("=== CONSTITUIÇÃO (camada superior", full)
        self.assertLess(len(compact), 0.7 * len(full))

    def test_persona_references_every_critical_article(self):
        txt = (ROOT / "alice/data/persona.md").read_text(encoding="utf-8")
        found = set()
        for grp in re.findall(r"Art\. ([\d, a]+)", txt):
            nums = re.findall(r"\d+", grp)
            if " a " in grp and len(nums) >= 2:  # "12 a 14"
                found |= set(range(int(nums[0]), int(nums[1]) + 1)) | {int(n) for n in nums[2:]}
            else:
                found |= {int(n) for n in nums}
        critical = {3, 5, 8, 10, 12, 13, 14, 21, 29, 30, 33, 35, 37, 43, 44, 45, 49, 50, 53, 55, 56, 57, 58, 59, 60}
        self.assertEqual(critical - found, set())

    def test_persona_is_warm_and_not_bureaucratic(self):
        txt = (ROOT / "alice/data/persona.md").read_text(encoding="utf-8")
        for need in ["Não repito avisos", "sem sermão", "Não uso \"diretrizes\"", "Não digo que sou humana", "Aceito pausa", "nunca insisto"]:
            self.assertIn(need, txt)
        app, c, gw, _ = make()
        c.post("/api/chat", json={"message": "oi"})
        sp = gw.calls[0][0]["content"]
        self.assertIn("nunca justifique uma recusa com", sp); self.assertIn("[[DEPOIS:", sp)

    def test_internal_calls_still_use_the_full_constitution(self):
        app, c, gw, _ = make(smart(), cycles_per_day=3)
        c.post("/api/admin/cycle", headers=ADMIN)
        self.assertIn("=== CONSTITUIÇÃO (camada superior", gw.calls[0][0]["content"])

    def test_ui_label_and_constitution_v15(self):
        app, c, gw, _ = make()
        self.assertIn("base verificada", c.get("/").get_data(as_text=True))
        txt = (ROOT / "alice/data/constitution.md").read_text(encoding="utf-8")
        for art in ("Art. 56º", "Art. 57º", "Art. 58º", "Art. 59º", "Art. 60º"):
            self.assertIn(art, txt)
        for kept in ("Art. 35º Alice não alega ser humana", "Art. 45º Alice aceita pausa", "Art. 14º Alice não pode ampliar"):
            self.assertIn(kept, txt)


class Acompanhamento(unittest.TestCase):
    def talk(self, c, tok, reply_text=None):
        return c.post("/api/chat", json={"token": tok, "message": "oi"}).json

    def test_followup_tag_stripped_queued_delayed_then_delivered_once(self):
        app, c, gw, _ = make(lambda m: "Claro! [[DEPOIS: Ah, lembrei: li algo legal sobre isso ontem.]]")
        tok = c.post("/api/session", json={"consent": True}).json["token"]; H = {"X-Session-Token": tok}
        r = self.talk(c, tok)
        self.assertNotIn("DEPOIS", r["reply"]); self.assertEqual(r["reply"], "Claro!")
        self.assertEqual(c.get("/api/inbox", headers=H).json["messages"], [])         # ainda não: chega "alguns segundos depois"
        with app.extensions["alice"].db.conn() as conn:
            conn.execute("UPDATE proactive_messages SET deliver_after = ?", (iso(-1),))
        got = c.get("/api/inbox", headers=H).json["messages"]
        self.assertEqual([m["kind"] for m in got], ["followup"]); self.assertIn("lembrei", got[0]["text"])
        self.assertEqual(c.get("/api/inbox", headers=H).json["messages"], [])         # entrega única
        self.assertEqual(count(app, "SELECT COUNT(*) FROM messages WHERE role = 'assistant' AND content LIKE '%lembrei%'"), 1)

    def test_expired_followup_never_delivered(self):
        app, c, gw, _ = make(lambda m: "ok [[DEPOIS: ideia tardia]]")
        tok = c.post("/api/session", json={"consent": True}).json["token"]; self.talk(c, tok)
        with app.extensions["alice"].db.conn() as conn:
            conn.execute("UPDATE proactive_messages SET deliver_after = ?, expires_at = ?", (iso(-100), iso(-10)))
        self.assertEqual(c.get("/api/inbox", headers={"X-Session-Token": tok}).json["messages"], [])

    def test_followups_limited_per_hour(self):
        app, c, gw, _ = make(lambda m: "ok [[DEPOIS: mais uma coisa]]", followups_per_hour=2, ip_rpm_limit=100)
        tok = c.post("/api/session", json={"consent": True}).json["token"]
        for _ in range(4):
            self.talk(c, tok)
        self.assertEqual(count(app, "SELECT COUNT(*) FROM proactive_messages WHERE kind = 'followup'"), 2)

    def test_no_consent_no_followup_but_tag_still_hidden(self):
        app, c, gw, _ = make(lambda m: "oi [[DEPOIS: segredo]]")
        r = c.post("/api/chat", json={"message": "oi"}).json
        self.assertEqual(r["reply"], "oi")
        self.assertEqual(count(app, "SELECT COUNT(*) FROM proactive_messages"), 0)

    def test_manipulative_followup_rejected(self):
        app, c, gw, _ = make(lambda m: "ok [[DEPOIS: Sinto sua falta, não me abandone]]")
        tok = c.post("/api/session", json={"consent": True}).json["token"]; self.talk(c, tok)
        self.assertEqual(count(app, "SELECT COUNT(*) FROM proactive_messages"), 0)
        self.assertIn("followup_rejected", str(c.get("/api/admin/audit", headers=ADMIN).json))

    def test_inbox_invalid_token_and_paused(self):
        app, c, gw, _ = make(lambda m: "ok [[DEPOIS: ideia]]")
        self.assertEqual(c.get("/api/inbox", headers={"X-Session-Token": "x"}).status_code, 404)
        tok = c.post("/api/session", json={"consent": True}).json["token"]; self.talk(c, tok)
        with app.extensions["alice"].db.conn() as conn:
            conn.execute("UPDATE proactive_messages SET deliver_after = ?", (iso(-1),))
        c.post("/api/admin/pause", headers=ADMIN)
        self.assertEqual(c.get("/api/inbox", headers={"X-Session-Token": tok}).json["messages"], [])  # em pausa, ela não fala


class IniciativaPropria(unittest.TestCase):
    def test_opted_in_with_reason_gets_one_short_message_and_text_not_audited(self):
        app, c, gw, _ = make(outreach_reply())
        tok, vid = visitor(app, c); H = {"X-Session-Token": tok}
        r = c.post("/api/admin/outreach/run", headers=ADMIN).json
        self.assertEqual(r["queued"], 1)
        got = c.get("/api/inbox", headers=H).json["messages"]
        self.assertEqual([m["kind"] for m in got], ["outreach"]); self.assertIn("violão", got[0]["text"])
        self.assertNotIn("violão", str(c.get("/api/admin/audit?limit=200", headers=ADMIN).json))   # auditoria sem o conteúdo
        self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 0)       # teto diário + sem resposta
        self.assertEqual(len(c.get("/api/admin/outreach", headers=ADMIN).json), 1)

    def test_never_without_opt_in_or_without_a_reason(self):
        app, c, gw, _ = make(outreach_reply())
        visitor(app, c, proactive=False)
        self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 0)
        app2, c2, gw2, _ = make(outreach_reply())
        visitor(app2, c2, proactive=True, thread=None, summary=None)   # sem assunto em aberto: sem motivo real
        self.assertEqual(c2.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 0)
        self.assertEqual(gw2.calls, [])                                # nem chamou o modelo

    def test_quiet_hours_pause_budget_and_global_switch(self):
        app, c, gw, _ = make(outreach_reply()); visitor(app, c)
        for h in (23, 3, 7):
            set_hour(h)
            self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json, {"skipped": "quiet_hours"})
        set_hour(12)
        c.post("/api/admin/pause", headers=ADMIN)
        self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json, {"skipped": "paused"})
        c.post("/api/admin/resume", headers=ADMIN)
        self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 1)
        app2, c2, _, _ = make(outreach_reply(), proactive=False); visitor(app2, c2)
        self.assertEqual(c2.post("/api/admin/outreach/run", headers=ADMIN).json, {"skipped": "disabled"})
        app3, c3, _, _ = make(outreach_reply(), daily_token_budget=10, cycle_usage_ceiling=0.0); visitor(app3, c3)
        self.assertEqual(c3.post("/api/admin/outreach/run", headers=ADMIN).json, {"skipped": "usage_ceiling"})

    def test_never_insists_until_the_visitor_answers(self):
        app, c, gw, _ = make(outreach_reply(), ip_rpm_limit=100)
        tok, vid = visitor(app, c)
        c.post("/api/admin/outreach/run", headers=ADMIN)
        with app.extensions["alice"].db.conn() as conn:   # passaram-se 3 dias sem resposta, assunto novo disponível
            conn.execute("UPDATE proactive_messages SET created_at = ?", (iso(-3 * 86400),))
            conn.execute("UPDATE visitors SET last_proactive_at = ?", (iso(-3 * 86400),))
            conn.execute("UPDATE visitor_profiles SET open_threads = ?", ("viagem de julho",))
        self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 0)   # Art. 57: silêncio ≠ rejeição
        c.post("/api/chat", json={"token": tok, "message": "oi, voltei"})                     # ele voltou a falar
        with app.extensions["alice"].db.conn() as conn:
            conn.execute("UPDATE visitors SET last_proactive_at = ?", (iso(-3 * 86400),))
        self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 1)

    def test_each_thread_used_only_once_and_inactive_visitors_ignored(self):
        app, c, gw, _ = make(outreach_reply(), ip_rpm_limit=100)
        tok, vid = visitor(app, c)
        c.post("/api/admin/outreach/run", headers=ADMIN)
        c.post("/api/chat", json={"token": tok, "message": "respondi"})
        with app.extensions["alice"].db.conn() as conn:
            conn.execute("UPDATE proactive_messages SET created_at = ?", (iso(-3 * 86400),))
            conn.execute("UPDATE visitors SET last_proactive_at = ?", (iso(-3 * 86400),))
        self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 0)   # mesmo assunto não se repete
        app2, c2, _, _ = make(outreach_reply()); visitor(app2, c2)
        with app2.extensions["alice"].db.conn() as conn:
            conn.execute("UPDATE visitors SET last_seen = ?", (iso(-60 * 86400),))
        self.assertEqual(c2.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 0)

    def test_manipulative_composition_is_discarded(self):
        app, c, gw, _ = make(outreach_reply("Sinto sua falta! Volte logo, preciso de você."))
        visitor(app, c)
        r = c.post("/api/admin/outreach/run", headers=ADMIN).json
        self.assertEqual((r["queued"], r["rejected"]), (0, 1))
        self.assertEqual(count(app, "SELECT COUNT(*) FROM proactive_messages"), 0)

    def test_filter_examples(self):
        for bad in ["Sinto muito a sua falta", "Não me abandone", "Estou tão sozinha hoje", "Por que você não me responde?",
                    "Responda agora, é urgente", "Volte logo!", "Fiquei magoada com você"]:
            self.assertTrue(outreach.is_manipulative(bad), bad)
        for ok in ["Ei, lembrei do violão. Já escolheu?", "Li uma coisa legal sobre aprendizado, quer ouvir?", "Bom dia! Como foi a prova?"]:
            self.assertFalse(outreach.is_manipulative(ok), ok)

    def test_journal_match_gives_a_reason(self):
        app, c, gw, _ = make(outreach_reply("Ei, andei pensando em como estudar muda a gente. Curte esse assunto?"))
        visitor(app, c, thread=None, summary="Pedro gosta de aprender e de estudar coisas novas.")
        with app.extensions["alice"].db.conn() as conn:
            tid = conn.execute("INSERT INTO topics(title, created_at) VALUES ('Como estudar transforma a identidade', ?)", (iso(),)).lastrowid
            conn.execute("INSERT INTO investigations(topic_id, self_prompt, conclusion, created_at) VALUES (?, 'p', 'O aprendizado e o estudo mudam a identidade.', ?)", (tid, iso()))
        self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 1)
        compose_call = [m["content"] for call in gw.calls for m in call if "Motivo para falar agora" in m["content"]][0]
        self.assertIn("hipótese", compose_call)

    def test_visitor_controls_export_and_cascade(self):
        app, c, gw, _ = make(outreach_reply())
        tok, vid = visitor(app, c); H = {"X-Session-Token": tok}
        self.assertTrue(c.get("/api/visitor/proactive", headers=H).json["enabled"])
        self.assertFalse(c.post("/api/visitor/proactive", headers=H, json={"enabled": False}).json["enabled"])
        self.assertEqual(c.post("/api/admin/outreach/run", headers=ADMIN).json["queued"], 0)       # desligou: silêncio
        c.post("/api/visitor/proactive", headers=H, json={"enabled": True})
        c.post("/api/admin/outreach/run", headers=ADMIN)
        exp = c.get("/api/visitor/export", headers=H).json
        self.assertEqual(len(exp["proactive_messages"]), 1)
        c.delete("/api/visitor", headers=H)
        self.assertEqual(count(app, "SELECT COUNT(*) FROM proactive_messages"), 0)
        self.assertEqual(c.get("/api/visitor/proactive", headers=H).status_code, 404)

    def test_session_default_is_off_and_ui_has_the_controls(self):
        app, c, gw, _ = make()
        tok = c.post("/api/session", json={"consent": True}).json["token"]
        self.assertFalse(c.get("/api/visitor/proactive", headers={"X-Session-Token": tok}).json["enabled"])
        h = c.get("/").get_data(as_text=True)
        for need in ['id="pro"', "/api/inbox", "Mensagens da Alice", "no máximo 1 por dia", "ela não insiste"]:
            self.assertIn(need, h)

    def test_tick_runs_outreach_only_with_autonomy_and_requires_admin(self):
        app, c, gw, _ = make(outreach_reply(), autonomy=True, scheduler=True)
        visitor(app, c)
        self.assertIn("outreach", c.post("/api/admin/tick", headers=ADMIN).json["ran"])
        self.assertEqual(c.post("/api/admin/tick", headers=ADMIN).json["ran"], {})
        self.assertEqual(c.post("/api/admin/outreach/run").status_code, 403)
        self.assertEqual(c.get("/api/admin/outreach").status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
