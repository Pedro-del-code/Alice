"""Ponto de entrada para produção (gunicorn wsgi:app). Use 1 worker: SQLite + agendador em processo único."""
import atexit

from alice.app import create_app
from alice.config import Config, password_warning
from alice.scheduler import start_scheduler

cfg = Config.from_env()
app = create_app(cfg)  # se houver armazenamento remoto e nenhum banco local, restaura aqui
remote = app.extensions["remote"]
if password_warning(cfg.admin_password):
    print("[aviso]", password_warning(cfg.admin_password))
if not (cfg.admin_password or cfg.admin_token):
    print("[aviso] sem ADMIN_PASSWORD/ADMIN_TOKEN: painel e rotas admin desativados.")
if cfg.remote_partial:
    print("[aviso] variáveis ALICE_S3_* preenchidas só em parte: armazenamento remoto DESLIGADO (preencha todas ou nenhuma).")
if remote.enabled and not cfg.backup_passphrase:
    print("[aviso] backup remoto SEM criptografia: defina ALICE_BACKUP_PASSPHRASE (ele contém conversas de visitantes).")
print(f"[info] armazenamento remoto: {remote.status()['startup']}")
if cfg.scheduler or remote.enabled:
    start_scheduler(app.extensions["alice"], cfg, remote)
if remote.enabled:
    atexit.register(remote.sync)  # envio final ao desligar (melhor esforço)
