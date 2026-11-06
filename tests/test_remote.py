import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))

from alice import audit, remote
from alice.app import create_app
from alice.config import Config
from alice.llm_gateway import MockGateway
from test_pensamento import smart

ADMIN = {"X-Admin-Token": "segredo-de-teste"}
AK, SK = "AKIDTESTE", "segredo/do+s3"


class FakeS3:
    """Servidor S3 mínimo que VERIFICA a assinatura SigV4 de cada requisição."""
    def __init__(self):
        self.storage, self.fail, self.requests = {}, False, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _auth_ok(self, body: bytes) -> bool:
                amzdate, ph = self.headers.get("x-amz-date", ""), self.headers.get("x-amz-content-sha256", "")
                if hashlib.sha256(body).hexdigest() != ph:
                    return False
                got = self.headers.get("Authorization", "")
                want, _ = remote.sign(self.command, self.headers["Host"], self.path, [],
                                      {"host": self.headers["Host"], "x-amz-content-sha256": ph, "x-amz-date": amzdate},
                                      ph, amzdate, "us-east-1", "s3", AK, SK)
                return got == want

            def _handle(self):
                outer.requests += 1
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                if outer.fail:
                    self.send_response(500); self.end_headers(); return
                if not self._auth_ok(body):
                    self.send_response(403); self.end_headers(); return
                key = self.path.lstrip("/").split("/", 1)[1]  # tira o bucket
                if self.command == "PUT":
                    outer.storage[key] = body; self.send_response(200); self.end_headers()
                elif key in outer.storage:
                    self.send_response(200); self.send_header("Content-Length", str(len(outer.storage[key]))); self.end_headers()
                    self.wfile.write(outer.storage[key])
                else:
                    self.send_response(404); self.end_headers()
            do_PUT = do_GET = _handle

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


def make(s3=None, tmp=None, passphrase="frase-de-teste", reply_fn=None, **over):
    tmp = tmp or tempfile.mkdtemp()
    kw = dict(db_path=os.path.join(tmp, "t.db"), admin_token="segredo-de-teste", **over)
    if s3:
        kw.update(s3_endpoint=s3.url, s3_bucket="bucket", s3_access_key=AK, s3_secret_key=SK, backup_passphrase=passphrase)
    cfg = Config(**kw)
    app = create_app(cfg, MockGateway(reply_fn))
    return app, app.test_client(), cfg


class SigV4(unittest.TestCase):
    def test_official_aws_test_vectors(self):
        h = {"host": "example.amazonaws.com", "x-amz-date": "20150830T123600Z"}
        _, sig = remote.sign("GET", "example.amazonaws.com", "/", [], h, hashlib.sha256(b"").hexdigest(),
                             "20150830T123600Z", "us-east-1", "service", "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
        self.assertEqual(sig, "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")  # suíte oficial: get-vanilla
        h2 = {"content-type": "application/x-www-form-urlencoded; charset=utf-8", "host": "iam.amazonaws.com", "x-amz-date": "20150830T123600Z"}
        _, sig2 = remote.sign("GET", "iam.amazonaws.com", "/", [("Action", "ListUsers"), ("Version", "2010-05-08")], h2,
                              hashlib.sha256(b"").hexdigest(), "20150830T123600Z", "us-east-1", "iam", "AKIDEXAMPLE",
                              "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
        self.assertEqual(sig2, "5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7")  # exemplo da documentação da AWS


class PersistenciaRemota(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self._sleep = remote.time.sleep
        remote.time.sleep = lambda s: None  # sem esperas nos testes de falha

    def tearDown(self):
        remote.time.sleep = self._sleep
        self.s3.close()

    def test_roundtrip_survives_losing_the_local_disk(self):
        tmp = tempfile.mkdtemp()
        app, c, cfg = make(self.s3, tmp)
        self.assertEqual(c.get("/api/admin/remote", headers=ADMIN).json["startup"], "no_remote")
        c.post("/api/admin/memories", headers=ADMIN, json={"category": "fact", "content": "memória que precisa sobreviver"})
        c.post("/api/admin/personality", headers=ADMIN, json={"traits": {"estilo": "versão dois"}, "justification": "teste"})
        self.assertEqual(c.post("/api/admin/remote/sync", headers=ADMIN).json["status"], "uploaded")
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        self.assertIn("alice/latest.db", self.s3.storage); self.assertIn(f"alice/daily-{day}.db", self.s3.storage)
        self.assertFalse(self.s3.storage["alice/latest.db"].startswith(remote.SQLITE_MAGIC))  # criptografado no repouso
        os.remove(cfg.db_path)  # "o Render apagou o disco"
        app2 = create_app(cfg, MockGateway()); c2 = app2.test_client()
        self.assertEqual(c2.get("/api/admin/remote", headers=ADMIN).json["startup"], "restored")
        self.assertEqual([m["content"] for m in c2.get("/api/admin/memories", headers=ADMIN).json], ["memória que precisa sobreviver"])
        self.assertEqual(c2.get("/api/admin/personality", headers=ADMIN).json["active"]["version"], 2)
        self.assertTrue(c2.get("/api/admin/audit/verify", headers=ADMIN).json["ok"])   # cadeia de auditoria íntegra
        self.assertIn("remote_restored", str(c2.get("/api/admin/audit", headers=ADMIN).json))

    def test_dirty_detection(self):
        app, c, cfg = make(self.s3)
        self.assertEqual(app.extensions["remote"].sync()["status"], "uploaded")
        self.assertEqual(app.extensions["remote"].sync()["status"], "unchanged")
        c.post("/api/admin/memories", headers=ADMIN, json={"category": "fact", "content": "mudou"})
        self.assertEqual(app.extensions["remote"].sync()["status"], "uploaded")

    def test_unencrypted_mode_and_wrong_passphrase(self):
        tmp = tempfile.mkdtemp()
        app, c, cfg = make(self.s3, tmp, passphrase="")
        c.post("/api/admin/remote/sync", headers=ADMIN)
        self.assertTrue(self.s3.storage["alice/latest.db"].startswith(remote.SQLITE_MAGIC))
        os.remove(cfg.db_path)
        create_app(cfg, MockGateway())  # restaura sem senha
        # agora criptografado + senha errada
        s3b = FakeS3(); tmp2 = tempfile.mkdtemp()
        try:
            appb, cb, cfgb = make(s3b, tmp2, passphrase="certa-certa-certa")
            cb.post("/api/admin/remote/sync", headers=ADMIN); os.remove(cfgb.db_path)
            cfgb.backup_passphrase = "errada"
            with self.assertRaises(remote.RemoteError):
                create_app(cfgb, MockGateway())
        finally:
            s3b.close()

    def test_unreachable_remote_with_no_local_db_refuses_to_start_and_never_clobbers(self):
        tmp = tempfile.mkdtemp()
        app, c, cfg = make(self.s3, tmp)
        c.post("/api/admin/remote/sync", headers=ADMIN)
        saved = dict(self.s3.storage)
        os.remove(cfg.db_path)
        self.s3.fail = True
        with self.assertRaises(remote.RemoteError):
            create_app(cfg, MockGateway())
        self.assertEqual(self.s3.storage, saved)           # nada foi sobrescrito
        self.assertFalse(os.path.exists(cfg.db_path))      # e não nasceu um banco vazio

    def test_corrupt_remote_backup_rejected(self):
        tmp = tempfile.mkdtemp()
        cfg_app, _, cfg = make(self.s3, tmp)
        self.s3.storage["alice/latest.db"] = remote.seal(b"isto nao e sqlite", "frase-de-teste")
        os.remove(cfg.db_path)
        with self.assertRaises(remote.RemoteError):
            create_app(cfg, MockGateway())

    def test_upload_failure_reported_not_raised(self):
        app, c, cfg = make(self.s3)
        self.s3.fail = True
        r = c.post("/api/admin/remote/sync", headers=ADMIN).json
        self.assertEqual(r["status"], "error")
        self.assertIsNotNone(c.get("/api/admin/remote", headers=ADMIN).json["last_error"])
        self.s3.fail = False
        self.assertEqual(c.post("/api/admin/remote/sync", headers=ADMIN).json["status"], "uploaded")

    def test_bad_credentials_rejected_by_server(self):
        app, c, cfg = make(self.s3)
        app.extensions["remote"].store._sk = "senha-errada"
        self.assertEqual(c.post("/api/admin/remote/sync", headers=ADMIN).json["status"], "error")
        self.assertEqual(self.s3.storage, {})

    def test_secrets_never_leak(self):
        app, c, cfg = make(self.s3); c.post("/api/admin/remote/sync", headers=ADMIN)
        blob = repr(cfg) + str(c.get("/api/admin/remote", headers=ADMIN).json) + str(c.get("/api/admin/audit?limit=200", headers=ADMIN).json) \
            + str(c.get("/api/admin/status", headers=ADMIN).json)
        for secret in (SK, "frase-de-teste"):
            self.assertNotIn(secret, blob)

    def test_maintenance_syncs_remote(self):
        app, c, cfg = make(self.s3)
        self.assertIn(c.post("/api/admin/maintenance", headers=ADMIN).json["remote"], ("uploaded", "unchanged"))

    def test_disabled_by_default(self):
        app, c, cfg = make()
        self.assertFalse(c.get("/api/admin/remote", headers=ADMIN).json["enabled"])
        self.assertEqual(c.post("/api/admin/remote/sync", headers=ADMIN).json["status"], "disabled")
        self.assertNotIn("remote", c.post("/api/admin/maintenance", headers=ADMIN).json)


class VariaveisOpcionais(unittest.TestCase):
    KEYS = ["ALICE_S3_ENDPOINT", "ALICE_S3_BUCKET", "ALICE_S3_REGION", "ALICE_S3_ACCESS_KEY", "ALICE_S3_SECRET_KEY",
            "ALICE_S3_PREFIX", "ALICE_BACKUP_PASSPHRASE"]

    def env(self, **vals):
        saved = {k: os.environ.get(k) for k in self.KEYS}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        for k in self.KEYS:
            os.environ.pop(k, None)
        os.environ.update(vals)
        return Config.from_env()

    def test_placeholders_count_as_empty(self):
        cfg = self.env(**{k: "-" for k in self.KEYS})
        self.assertFalse(cfg.remote_enabled); self.assertFalse(cfg.remote_partial)
        self.assertEqual(cfg.s3_region, "us-east-1"); self.assertEqual(cfg.backup_passphrase, "")
        cfg2 = self.env(ALICE_S3_ENDPOINT="  nenhum ", ALICE_S3_BUCKET="")
        self.assertFalse(cfg2.remote_enabled)

    def test_all_filled_enables_and_trims_spaces(self):
        cfg = self.env(ALICE_S3_ENDPOINT=" https://x.example ", ALICE_S3_BUCKET="b ", ALICE_S3_ACCESS_KEY=" k", ALICE_S3_SECRET_KEY="s ")
        self.assertTrue(cfg.remote_enabled)
        self.assertEqual((cfg.s3_endpoint, cfg.s3_bucket, cfg.s3_access_key, cfg.s3_secret_key), ("https://x.example", "b", "k", "s"))

    def test_partial_is_disabled_and_flagged(self):
        cfg = self.env(ALICE_S3_ENDPOINT="https://x.example", ALICE_S3_BUCKET="b")
        self.assertFalse(cfg.remote_enabled); self.assertTrue(cfg.remote_partial)
        app = create_app(Config(db_path=os.path.join(tempfile.mkdtemp(), "t.db"), admin_token="t",
                                s3_endpoint="https://x.example", s3_bucket="b"), MockGateway())
        self.assertFalse(app.extensions["remote"].enabled)   # parcial → nada de rede, o serviço inicia normalmente


class AgendamentoPersistente(unittest.TestCase):
    def test_tick_runs_only_what_is_due_and_survives_restarts(self):
        tmp = tempfile.mkdtemp()
        app, c, cfg = make(tmp=tmp, reply_fn=smart(), autonomy=True, scheduler=True, cycles_per_day=5)
        first = c.post("/api/admin/tick", headers=ADMIN).json["ran"]
        self.assertTrue({"cycle", "maintenance", "reflection"} <= set(first))
        self.assertEqual(c.post("/api/admin/tick", headers=ADMIN).json["ran"], {})             # nada vencido
        app2 = create_app(cfg, MockGateway(smart())); c2 = app2.test_client()                   # "reinício"
        self.assertEqual(c2.post("/api/admin/tick", headers=ADMIN).json["ran"], {})             # não dispara em sequência
        with app2.extensions["alice"].db.conn() as conn:
            conn.execute("UPDATE system_state SET value = '0' WHERE key = 'ts_cycle'")
        self.assertEqual(set(c2.post("/api/admin/tick", headers=ADMIN).json["ran"]), {"cycle"})

    def test_tick_without_autonomy_only_maintains(self):
        app, c, cfg = make(autonomy=False)
        self.assertEqual(set(c.post("/api/admin/tick", headers=ADMIN).json["ran"]), {"maintenance"})

    def test_tick_requires_admin(self):
        app, c, cfg = make()
        self.assertEqual(c.post("/api/admin/tick").status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
