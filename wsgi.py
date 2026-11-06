"""Ponto de entrada para produção (gunicorn wsgi:app). Use 1 worker: SQLite + agendador em processo único."""
from alice.app import create_app
from alice.config import Config, password_warning
from alice.scheduler import start_scheduler

cfg = Config.from_env()
app = create_app(cfg)
if password_warning(cfg.admin_password):
    print("[aviso]", password_warning(cfg.admin_password))
if not (cfg.admin_password or cfg.admin_token):
    print("[aviso] sem ADMIN_PASSWORD/ADMIN_TOKEN: painel e rotas admin desativados.")
if cfg.scheduler:
    start_scheduler(app.extensions["alice"], cfg)
