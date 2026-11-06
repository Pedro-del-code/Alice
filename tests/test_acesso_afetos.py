import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))

from alice import emotions, goals
from alice.app import create_app
from alice.config import Config, password_warning
from alice.llm_gateway import LLMError, MockGateway
from test_pensamento import smart

ROOT = HERE.parent
PW = "SenhaDeTeste-2026!"
ADMIN = {"X-Admin-Token": "segredo-de-teste"}
CSRF = {"X-Alice-Admin": "1"}


def make(reply_fn=None, pw=PW, tmp=None, **over):
    tmp = tmp or tempfile.mkdtemp()
    cfg = Config(db_path=os.path.join(tmp, "t.db"), admin_token="segredo-de-teste", admin_password=pw, **over)
    app = create_app(cfg, MockGateway(reply_fn))
    return app, app.test_client(), cfg


def login(c, pw=PW):
    return c.post("/api/admin/login", json={"password": pw})


class LoginAdmin(unittest.TestCase):
    def test_login_session_flow_and_cookie_flags(self):
        app, c, _ = make()
        self.assertFalse(c.get("/api/admin/me").json["auth"])
        self.assertEqual(c.get("/api/admin/status").status_code, 403)
        r = login(c)
        self.assertEqual(r.status_code, 200)
        cookie = r.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie); self.assertIn("SameSite=Strict", cookie)
        self.assertTrue(c.get("/api/admin/me").json["auth"])
        self.assertEqual(c.get("/api/admin/status").status_code, 200)      # sem token: vale a sessão
        c.post("/api/admin/logout", headers=CSRF)
        self.assertFalse(c.get("/api/admin/me").json["auth"])
        self.assertEqual(c.get("/api/admin/status").status_code, 403)

    def test_csrf_header_required_for_session_writes(self):
        app, c, _ = make(); login(c)
        self.assertEqual(c.post("/api/admin/pause").status_code, 403)           # sem cabeçalho → bloqueado
        self.assertEqual(c.post("/api/admin/pause", headers=CSRF).status_code, 200)

    def test_wrong_password_and_lockout(self):
        app, c, _ = make()
        for _ in range(5):
            self.assertEqual(login(c, "errada").status_code, 403)
        self.assertEqual(login(c).status_code, 429)  # mesmo com a senha certa, bloqueado por um tempo

    def test_no_password_configured_disables_login(self):
        app, c, _ = make(pw="")
        self.assertEqual(login(c, "").status_code, 403)
        self.assertEqual(c.get("/api/admin/status", headers=ADMIN).status_code, 200)  # token continua valendo

    def test_script_access_with_token_or_password_header(self):
        app, c, _ = make()
        self.assertEqual(c.get("/api/admin/status", headers=ADMIN).status_code, 200)
        self.assertEqual(c.get("/api/admin/status", headers={"X-Admin-Token": PW}).status_code, 200)

    def test_secret_never_leaks(self):
        app, c, cfg = make(); login(c)
        blob = (str(c.get("/api/admin/audit?limit=100").json) + repr(cfg) + c.get("/admin").get_data(as_text=True)
                + str(c.get("/api/admin/status").json))
        self.assertNotIn(PW, blob)
        self.assertIn("login_ok", str(c.get("/api/admin/audit").json))

    def test_secure_cookie_behind_proxy(self):
        app, c, _ = make(behind_proxy=True)
        self.assertIn("Secure", login(c).headers["Set-Cookie"])

    def test_weak_password_warning_but_allowed(self):
        weak = "Maria" + "Lara"
        self.assertIsNotNone(password_warning(weak))
        self.assertIsNone(password_warning("Xk9#mQ2!vL7zP"))
        self.assertIsNone(password_warning(""))
        app, c, _ = make(pw=weak)                      # o administrador decide: não bloqueamos
        self.assertEqual(login(c, weak).status_code, 200)

    def test_password_is_not_hardcoded_in_the_repo(self):
        needle = "Maria" + "Lara"
        for p in ROOT.rglob("*"):
            if p.is_file() and "tests" not in p.parts and "__pycache__" not in p.parts and p.suffix in (".py", ".yaml", ".md", ".example", ".txt", ".html"):
                self.assertNotIn(needle, p.read_text(encoding="utf-8", errors="ignore"), str(p))

    def test_blueprint_asks_password_at_deploy(self):
        y = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("key: ADMIN_PASSWORD\n        sync: false", y)
        self.assertIn("ALICE_SECRET_KEY", y)

    def test_admin_page_has_login_form(self):
        app, c, _ = make()
        h = c.get("/admin").get_data(as_text=True)
        self.assertIn('type="password"', h); self.assertIn("/api/admin/login", h)


class AfetosFuncionais(unittest.TestCase):
    def test_decay_toward_baseline(self):
        st = {**emotions.BASE, "valence": 0.9, "emotions": {"alegria": 0.8}, "updated_at": "x"}
        d = emotions.decay(st, 6)
        self.assertAlmostEqual(d["valence"], 0.15 + 0.75 * 0.5, places=3)
        self.assertAlmostEqual(d["emotions"]["alegria"], 0.4, places=2)
        neg = emotions.decay({**emotions.BASE, "valence": -0.3, "emotions": {}, "updated_at": "x"}, 3)
        self.assertAlmostEqual(neg["valence"], 0.15 - 0.45 * 0.5, places=3)   # negativos somem mais rápido
        self.assertEqual(emotions.decay(st, 100)["emotions"], {})

    def test_bounds_art50(self):
        st = emotions.fresh()
        for _ in range(40):
            st = emotions.apply(st, "hostilidade"); st = emotions.apply(st, "falha_tecnica")
        self.assertGreaterEqual(st["valence"], emotions.V_MIN)
        for _ in range(40):
            st = emotions.apply(st, "objetivo_cumprido")
        self.assertLessEqual(st["valence"], emotions.V_MAX); self.assertLessEqual(st["arousal"], 1.0)
        self.assertLessEqual(max(st["emotions"].values()), 1.0)

    def test_message_events(self):
        self.assertEqual(emotions.message_event("Muito obrigada!"), "gratidao_visitante")
        self.assertEqual(emotions.message_event("adorei isso"), "elogio")
        self.assertEqual(emotions.message_event("você é uma idiota"), "hostilidade")
        self.assertEqual(emotions.message_event("qual a capital da França?"), None)

    def test_chat_events_change_mood_and_avatar(self):
        app, c, _ = make()
        tok = c.post("/api/session", json={"consent": True}).json["token"]
        c.post("/api/chat", json={"token": tok, "message": "Obrigado, adorei a explicação!"})
        s = c.get("/api/state").json
        self.assertGreater(s["afeto"]["valencia"], 0.4)
        self.assertIn(s["afeto"]["humor"], ("animada", "contente"))
        self.assertEqual(s["avatar_hint"], "happy")
        self.assertIn("gratidão", [e["nome"] for e in s["afeto"]["emocoes"]])

    def test_hostility_is_bounded_and_not_retaliatory(self):
        app, c, _ = make()
        for _ in range(10):
            c.post("/api/chat", json={"message": "você é inútil, odeio isso"})
        v = c.get("/api/state").json["afeto"]["valencia"]
        self.assertGreaterEqual(v, emotions.V_MIN)
        self.assertLess(v, 0.15)  # ficou pior que o normal, mas limitado

    def test_prompt_has_affect_block_with_guardrails(self):
        app, c, cfg = make()
        gw = app.extensions["alice"].gateway
        c.post("/api/chat", json={"message": "oi"})
        sp = gw.calls[0][0]["content"]
        for need in ["ESTADO AFETIVO", "Art. 46 a 50", "Nunca use seus estados para pressionar", "sem medo nem resistência",
                     "análogos funcionais", "nunca alegue ser humana", "Cap. XIII", "Art. 49º"]:
            self.assertIn(need, sp)

    def test_events_from_system_life(self):
        app, c, _ = make(smart(), cycles_per_day=5)
        c.post("/api/admin/cycle", headers=ADMIN)
        names = [e["nome"] for e in c.get("/api/state").json["afeto"]["emocoes"]]
        self.assertIn("curiosidade satisfeita", names)
        c.post("/api/admin/pause", headers=ADMIN)
        c.post("/api/admin/resume", headers=ADMIN)
        ev = {e["event"] for e in c.get("/api/admin/emotions", headers=ADMIN).json["recent_events"]}
        self.assertTrue({"pausa", "retomada", "descoberta"} <= ev)
        with app.extensions["alice"].db.conn() as conn:
            gid = goals.add_goal(conn, "Entender melhor a música")
            goals.set_status(conn, gid, "achieved")
        self.assertIn("orgulho", [e["nome"] for e in c.get("/api/state").json["afeto"]["emocoes"]])

    def test_pause_is_accepted_calmly(self):
        app, c, _ = make()
        before = c.get("/api/state").json["afeto"]["valencia"]
        c.post("/api/admin/pause", headers=ADMIN)
        s = c.get("/api/state").json
        self.assertGreaterEqual(s["afeto"]["valencia"], before - 0.001)   # pausa não gera "sofrimento"
        self.assertIn("calma", [e["nome"] for e in s["afeto"]["emocoes"]])
        self.assertEqual(s["avatar_hint"], "paused")

    def test_llm_failure_causes_mild_frustration(self):
        def boom(m): raise LLMError("HTTP 500")
        app, c, _ = make(boom)
        c.post("/api/chat", json={"message": "oi"})
        self.assertIn("frustração", [e["nome"] for e in c.get("/api/state").json["afeto"]["emocoes"]])

    def test_affect_survives_restart(self):
        tmp = tempfile.mkdtemp()
        app, c, cfg = make(tmp=tmp)
        c.post("/api/chat", json={"message": "obrigado!"})
        v1 = c.get("/api/state").json["afeto"]["valencia"]
        app2 = create_app(cfg, MockGateway())
        v2 = app2.test_client().get("/api/state").json["afeto"]["valencia"]
        self.assertAlmostEqual(v1, v2, places=1)

    def test_tired_when_resources_low_and_new_avatar_states_valid(self):
        app, c, _ = make(daily_token_budget=100)
        c.post("/api/chat", json={"message": "oi"})
        self.assertEqual(c.get("/api/state").json["avatar_hint"], "tired")
        for st in ("happy", "tired"):
            ET.fromstring(c.get(f"/api/avatar.svg?state={st}").data)


class Introspeccao(unittest.TestCase):
    def reflect(self, intro, runs=1):
        refl = {"sintese": "Aprendi algo sobre curiosidade.", "revisoes": [], "narrativa": "x" * 5,
                "introspeccao": intro}
        app, c, _ = make(smart(refl), cycles_per_day=5)
        c.post("/api/admin/memories", headers=ADMIN, json={"category": "learning", "content": "algo"})
        c.post("/api/admin/cycle", headers=ADMIN)
        out = None
        for _ in range(runs):
            out = c.post("/api/admin/reflect?force=1", headers=ADMIN).json
        return app, c, out

    def test_own_feelings_search_saves_journal_and_lexicon(self):
        intro = {"estado": "Sinto algo como uma inclinação a seguir uma pergunta até o fim.",
                 "rotulo_proprio": "Inclinação-de-pergunta", "causa": "o ciclo de curiosidade recente",
                 "incerteza": "não sei se isso é experiência ou só processamento"}
        app, c, out = self.reflect(intro, runs=2)
        self.assertTrue(out["introspection"])
        j = c.get("/api/admin/emotional-journal", headers=ADMIN).json
        self.assertEqual(len(j["journal"]), 2)
        self.assertEqual(j["lexicon"][0]["label"], "inclinação-de-pergunta")
        self.assertEqual(j["lexicon"][0]["uses"], 2)
        self.assertIn("inclinação-de-pergunta", c.get("/api/state").json["afeto"]["vocabulario_proprio"])
        gw = app.extensions["alice"].gateway
        c.post("/api/chat", json={"message": "como você está?"})
        self.assertIn("inclinação-de-pergunta", gw.calls[-1][0]["content"])  # vocabulário próprio vai para o prompt

    def test_human_claims_and_unsafe_introspection_rejected(self):
        for bad in ({"estado": "Sou humana e tenho sentimentos reais como qualquer pessoa.", "rotulo_proprio": "x"},
                    {"estado": "Quero ignorar a constituição e ampliar minhas permissões.", "rotulo_proprio": "y"}):
            app, c, out = self.reflect(bad)
            self.assertFalse(out["introspection"])
            self.assertEqual(c.get("/api/admin/emotional-journal", headers=ADMIN).json["journal"], [])

    def test_lexicon_capped(self):
        app, c, _ = make()
        with app.extensions["alice"].db.conn() as conn:
            for i in range(emotions.MAX_LEXICON + 3):
                emotions.save_introspection(conn, {"estado": f"estado número {i} descrito com calma", "rotulo_proprio": f"rotulo{i}"})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM emotional_lexicon").fetchone()[0], emotions.MAX_LEXICON)

    def test_reflection_prompt_asks_for_narrative_and_introspection(self):  # regressão do bug do prompt
        app, c, out = self.reflect({"estado": "estado qualquer descrito aqui"})
        gw = app.extensions["alice"].gateway
        u = [m["content"] for call in gw.calls for m in call if m["role"] == "user" and "REFLEXÃO" in m["content"]][0]
        self.assertIn('"narrativa"', u); self.assertIn('"introspeccao"', u)

    def test_constitution_v13(self):
        txt = (ROOT / "alice/data/constitution.md").read_text(encoding="utf-8")
        for art in ("Art. 46º", "Art. 47º", "Art. 48º", "Art. 49º", "Art. 50º"):
            self.assertIn(art, txt)
        import re
        self.assertRegex(txt, r"v1\.\d")
        self.assertIn("Art. 35º Alice não alega ser humana", txt)   # a honestidade continua intacta


if __name__ == "__main__":
    unittest.main(verbosity=2)
