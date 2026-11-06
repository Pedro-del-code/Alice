import os

from alice.app import create_app
from alice.config import Config, password_warning
from alice.scheduler import start_scheduler

if __name__ == "__main__":
    cfg = Config.from_env()
    app = create_app(cfg)
    if not cfg.groq_api_key:
        print("[aviso] GROQ_API_KEY ausente: rodando em MODO SIMULADO.")
    if not (cfg.admin_password or cfg.admin_token):
        print("[aviso] sem ADMIN_PASSWORD/ADMIN_TOKEN: painel e rotas admin desativados.")
    if password_warning(cfg.admin_password):
        print("[aviso]", password_warning(cfg.admin_password))
    if cfg.scheduler:
        start_scheduler(app.extensions["alice"], cfg)
    # 127.0.0.1 por padrão (ambiente controlado, Art. 34). Em produção use wsgi.py/gunicorn.
    app.run(host=os.environ.get("ALICE_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=False)
