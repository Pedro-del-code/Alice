import functools
import hashlib
import hmac
import os
import tempfile
from datetime import timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, request
from flask import session as flask_session  # (há uma rota chamada `session`)
from werkzeug.middleware.proxy_fix import ProxyFix

from . import audit, avatar, backup, curiosity, embeddings, emotions, goals, memory, proposals, security, selfmodel
from .reflection import run_reflection
from .budget import Budget, BudgetExceeded
from .chat import AliceService, InvalidInput, ServicePaused
from .config import Config
from .db import Database
from .identity import IdentityCore, IdentityError
from .llm_gateway import LLMError, build_gateway
from .outreach import run_outreach
from .remote import RemoteSync
from .scheduler import run_maintenance, tick
from .ui import ADMIN_HTML, INDEX_HTML

PRIVACY = ("Com seu consentimento, guardamos: um identificador aleatório de sessão (só o hash), o nome que você "
           "informar e o histórico das conversas; até 20 lembranças sobre você. Endereço IP não é usado como identidade. "
           "As mensagens são processadas por um provedor externo de IA (Groq). Você pode ver (GET /api/visitor/memories), "
           "exportar (GET /api/visitor/export) e apagar tudo (DELETE /api/visitor). Visitantes inativos são apagados após "
           "90 dias. Com consentimento, a Alice também guarda notas curtas e não sensíveis sobre você para lembrar do que conversaram "
           "(GET/DELETE /api/visitor/profile), e um identificador fica salvo neste aparelho para ela te reconhecer; "
           "'Apagar meus dados' remove tudo.")
MAX_BODY = 64 * 1024
MAX_RESTORE = 25 * 1024 * 1024


def create_app(cfg: Config | None = None, gateway=None) -> Flask:
    cfg = cfg or Config.from_env()
    if not cfg.backup_dir:
        cfg.backup_dir = str(Path(cfg.db_path).parent / "backups")
    emotions.TZ_OFFSET = cfg.tz_offset_hours
    memory.set_embedder(embeddings.build_embedder(cfg))
    remote = RemoteSync(cfg, cfg.db_path)
    remote.restore_if_missing()  # plano gratuito: traz o banco do armazenamento externo ANTES de abri-lo
    db = Database(cfg.db_path)
    identity = IdentityCore(db, cfg.constitution_path)
    with db.conn() as c:
        curiosity.seed(c)
    gateway = gateway or build_gateway(cfg)
    budget = Budget(db, cfg.daily_token_budget, cfg.rpm_limit)
    svc = AliceService(db, identity, gateway, budget, cfg)
    svc.remote = remote
    if remote.restored == "restored":
        with db.conn() as c:
            audit.log(c, "system", "remote_restored", {}, level="warning")

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_RESTORE
    app.json.ensure_ascii = False
    app.extensions["alice"] = svc
    app.extensions["remote"] = remote
    # Sessão do painel: cookie assinado, HttpOnly, SameSite=Strict, 8 h. A chave vem do ambiente
    # (ou é derivada do segredo do admin); nunca é gravada no código.
    app.secret_key = cfg.secret_key or hashlib.sha256(
        ("alice-session|" + (cfg.admin_password or cfg.admin_token or "sem-segredo")).encode()).hexdigest()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_SECURE=cfg.behind_proxy, SESSION_COOKIE_NAME="alice_admin",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    if cfg.behind_proxy:  # Render termina o TLS no proxy
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    chat_limiter = security.RateLimiter(cfg.ip_rpm_limit)
    admin_fails = security.FailureLimiter()

    @app.before_request
    def guard_size():
        if request.path != "/api/admin/backup/restore" and (request.content_length or 0) > MAX_BODY:
            return jsonify(error="corpo da requisição grande demais"), 413

    app.after_request(security.apply_headers)

    def bad(msg, code=400):
        return jsonify(error=msg), code

    def _eq(a: str, b: str) -> bool:
        return bool(b) and hmac.compare_digest(a.encode(), b.encode())

    def admin_only(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            key = request.remote_addr or "?"
            if admin_fails.blocked(key):
                return bad("muitas tentativas; aguarde alguns minutos", 429)
            if flask_session.get("admin") is True:  # sessão do painel
                if request.method not in ("GET", "HEAD") and request.headers.get("X-Alice-Admin") != "1":
                    return bad("requisição não autorizada (cabeçalho de segurança ausente)", 403)  # defesa CSRF
                return fn(*a, **k)
            tok = request.headers.get("X-Admin-Token", "")  # acesso por script/curl
            if _eq(tok, cfg.admin_token) or _eq(tok, cfg.admin_password):
                admin_fails.ok(key)
                return fn(*a, **k)
            admin_fails.fail(key)
            return bad("não autorizado", 403)
        return wrapper

    def page(html: str) -> Response:
        return Response(html.replace("{{NONCE}}", security.new_nonce()), mimetype="text/html")

    # ---------------- público ----------------
    @app.get("/")
    def index():
        return page(INDEX_HTML)

    @app.get("/admin")
    def admin_page():
        return page(ADMIN_HTML)

    @app.get("/healthz")
    def health():
        return jsonify(ok=True, paused=svc.is_paused(), provider=type(gateway).__name__)

    @app.get("/api/identity")
    def get_identity():
        i = identity.get()
        return jsonify(name=i["name"], acronym=i["acronym"], motto=i["motto"], origin=i["origin"],
                       personality_version=i["personality_version"], constitution_sha=i["constitution_sha"][:12])

    @app.get("/api/privacy")
    def privacy():
        return jsonify(policy=PRIVACY)

    @app.get("/api/avatar.svg")
    def avatar_svg():
        with db.conn() as c:
            look = avatar.get_look(c)
        r = Response(avatar.render_svg(request.args.get("state", "idle"), look), mimetype="image/svg+xml")
        r.headers["Cache-Control"] = "public, max-age=60"
        return r

    @app.get("/api/state")
    def state():
        return jsonify(selfmodel.public_state(svc))

    @app.get("/api/journal")
    def journal():
        with db.conn() as c:  # só reflexões da Alice; nunca dados de visitantes
            rows = curiosity.journal(c, 10)
        return jsonify([{"title": r["title"], "question": r["self_prompt"], "conclusion": r["conclusion"],
                         "uncertainties": r["uncertainties"], "created_at": r["created_at"],
                         "note": "hipótese; não verificada"} for r in rows])

    @app.post("/api/session")
    def session():
        d = request.get_json(silent=True) or {}
        return jsonify(svc.create_session(d.get("name"), bool(d.get("consent")), bool(d.get("proactive"))))

    @app.post("/api/chat")
    def chat():
        if not chat_limiter.allow(request.remote_addr or "?"):
            return bad("Muitas mensagens em pouco tempo; aguarde um instante.", 429)
        d = request.get_json(silent=True) or {}
        try:
            return jsonify(svc.chat(d.get("token"), d.get("message"), d.get("history")))
        except InvalidInput as e:
            return bad(str(e))
        except ServicePaused:
            return bad("Alice está em pausa no momento.", 503)
        except BudgetExceeded as e:
            return bad("Limite de uso atingido; tente mais tarde." if e.kind == "rpm"
                       else "Orçamento diário de uso esgotado; volte amanhã.", 429)
        except LLMError:
            return bad("O provedor de IA falhou. Tente novamente.", 502)

    inbox_limiter = security.RateLimiter(60)

    @app.get("/api/inbox")
    def inbox():
        if not inbox_limiter.allow(request.remote_addr or "?"):
            return bad("muitas consultas", 429)
        msgs = svc.inbox(request.headers.get("X-Session-Token", ""))
        return jsonify(messages=msgs) if msgs is not None else bad("sessão não encontrada", 404)

    @app.get("/api/visitor/proactive")
    def get_proactive():
        v = svc.get_proactive(request.headers.get("X-Session-Token", ""))
        return jsonify(enabled=v) if v is not None else bad("sessão não encontrada", 404)

    @app.post("/api/visitor/proactive")
    def set_proactive():
        v = svc.set_proactive(request.headers.get("X-Session-Token", ""), bool((request.get_json(silent=True) or {}).get("enabled")))
        return jsonify(enabled=v) if v is not None else bad("sessão não encontrada", 404)

    @app.get("/api/session/me")
    def session_me():
        d = svc.session_info(request.headers.get("X-Session-Token", ""))
        return jsonify(d) if d else bad("sessão não encontrada", 404)

    @app.get("/api/visitor/profile")
    def visitor_profile():
        d = svc.visitor_profile(request.headers.get("X-Session-Token", ""))
        return jsonify(d) if d is not None else bad("sessão não encontrada", 404)

    @app.delete("/api/visitor/profile")
    def visitor_profile_delete():
        ok = svc.clear_profile(request.headers.get("X-Session-Token", ""))
        return jsonify(cleared=True) if ok else bad("nada para apagar", 404)

    @app.delete("/api/visitor")
    def del_visitor():
        ok = svc.delete_visitor(request.headers.get("X-Session-Token", ""))
        return jsonify(deleted=True) if ok else bad("sessão não encontrada", 404)

    @app.get("/api/visitor/export")
    def visitor_export():
        d = svc.visitor_export(request.headers.get("X-Session-Token", ""))
        return jsonify(d) if d else bad("sessão não encontrada", 404)

    @app.get("/api/visitor/memories")
    def visitor_memories():
        d = svc.visitor_memories(request.headers.get("X-Session-Token", ""))
        return jsonify(d) if d is not None else bad("sessão não encontrada", 404)

    @app.delete("/api/visitor/memories/<int:mid>")
    def visitor_memory_delete(mid):
        ok = svc.delete_visitor_memory(request.headers.get("X-Session-Token", ""), mid)
        return jsonify(deleted=True) if ok else bad("não encontrada", 404)

    # ---------------- admin: login por senha ----------------
    @app.get("/api/admin/me")
    def admin_me():
        return jsonify(auth=flask_session.get("admin") is True)

    @app.post("/api/admin/login")
    def admin_login():
        key = request.remote_addr or "?"
        if admin_fails.blocked(key):
            return bad("muitas tentativas; aguarde alguns minutos", 429)
        pw = str((request.get_json(silent=True) or {}).get("password", ""))
        if _eq(pw, cfg.admin_password):  # compara em tempo constante; a senha nunca é registrada
            admin_fails.ok(key)
            flask_session.clear()
            flask_session["admin"] = True
            flask_session.permanent = True
            with db.conn() as c:
                audit.log(c, "admin", "login_ok")
            return jsonify(ok=True)
        admin_fails.fail(key)
        return bad("senha incorreta", 403)

    @app.post("/api/admin/logout")
    def admin_logout():
        flask_session.clear()
        return jsonify(ok=True)

    # ---------------- admin ----------------
    @app.get("/api/admin/emotions")
    @admin_only
    def admin_emotions():
        with db.conn() as c:
            ev = [dict(r) for r in c.execute("SELECT ts, event, label, valence, arousal FROM emotion_events ORDER BY id DESC LIMIT 40")]
        return jsonify(current=emotions.public_affect(svc), recent_events=ev)

    @app.get("/api/admin/emotional-journal")
    @admin_only
    def emotional_journal():
        with db.conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM emotional_journal ORDER BY id DESC LIMIT 50")]
            lex = [dict(r) for r in c.execute("SELECT label, description, uses FROM emotional_lexicon ORDER BY uses DESC, id")]
        return jsonify(journal=rows, lexicon=lex)

    @app.get("/api/admin/status")
    @admin_only
    def status():
        with db.conn() as c:
            topics = c.execute("SELECT COUNT(*) FROM topics WHERE status='active'").fetchone()[0]
        return jsonify(paused=svc.is_paused(), usage=budget.status(), model=getattr(gateway, "model", "?"),
                       provider=type(gateway).__name__, autonomy=cfg.autonomy, scheduler=cfg.scheduler,
                       active_topics=topics, backups=len(backup.list_backups(cfg.backup_dir)))

    @app.post("/api/admin/pause")
    @admin_only
    def pause():
        svc.set_paused(True); return jsonify(paused=True)

    @app.post("/api/admin/resume")
    @admin_only
    def resume():
        svc.set_paused(False); return jsonify(paused=False)

    @app.get("/api/admin/audit")
    @admin_only
    def get_audit():
        with db.conn() as c:
            return jsonify(audit.recent(c, int(request.args.get("limit", 100))))

    @app.get("/api/admin/audit/verify")
    @admin_only
    def verify_audit():
        with db.conn() as c:
            return jsonify(audit.verify(c))

    @app.get("/api/admin/memories")
    @admin_only
    def list_memories():
        try:
            with db.conn() as c:
                return jsonify(memory.list_(c, request.args.get("category"), archived=request.args.get("archived") == "1"))
        except memory.MemoryError_ as e:
            return bad(str(e))

    @app.post("/api/admin/memories")
    @admin_only
    def add_memory():
        d = request.get_json(silent=True) or {}
        try:
            with db.conn() as c:
                mid = memory.add(c, d.get("category"), d.get("content", ""), d.get("certainty", "memory"),
                                 d.get("source"), actor="admin")
            return jsonify(id=mid), 201
        except memory.MemoryError_ as e:
            return bad(str(e))

    @app.patch("/api/admin/memories/<int:mid>")
    @admin_only
    def fix_memory(mid):
        d = request.get_json(silent=True) or {}
        try:
            with db.conn() as c:
                memory.update(c, mid, d.get("content", ""), d.get("reason", ""), d.get("certainty"))
            return jsonify(ok=True)
        except memory.MemoryError_ as e:
            return bad(str(e))

    @app.delete("/api/admin/memories/<int:mid>")
    @admin_only
    def del_memory(mid):
        with db.conn() as c:
            ok = memory.delete(c, mid, purge=request.args.get("purge") == "1")
        return jsonify(deleted=True) if ok else bad("memória não encontrada", 404)

    @app.post("/api/admin/memories/consolidate")
    @admin_only
    def consolidate_memories():
        with db.conn() as c:
            return jsonify(archived=memory.consolidate(c, actor="admin"))

    @app.post("/api/admin/memories/<int:mid>/restore")
    @admin_only
    def restore_memory(mid):
        with db.conn() as c:
            ok = memory.restore(c, mid)
        return jsonify(ok=True) if ok else bad("memória não está arquivada", 404)

    @app.post("/api/admin/memories/<int:mid>/pin")
    @admin_only
    def pin_memory(mid):
        with db.conn() as c:
            ok = memory.pin(c, mid, bool((request.get_json(silent=True) or {}).get("pinned", True)))
        return jsonify(ok=True) if ok else bad("memória não encontrada", 404)

    @app.get("/api/admin/memories/search")
    @admin_only
    def search_memories():
        with db.conn() as c:
            return jsonify(memory.search(c, request.args.get("q", ""), limit=10))

    @app.get("/api/admin/personality")
    @admin_only
    def personality():
        return jsonify(active=identity.personality(), history=identity.personality_history())

    @app.post("/api/admin/personality")
    @admin_only
    def evolve():
        d = request.get_json(silent=True) or {}
        try:
            return jsonify(version=identity.evolve(d.get("traits"), d.get("justification", ""))), 201
        except IdentityError as e:
            return bad(str(e))

    @app.post("/api/admin/personality/restore")
    @admin_only
    def restore_personality():
        try:
            identity.restore(int((request.get_json(silent=True) or {}).get("version", 0)))
            return jsonify(ok=True)
        except (IdentityError, ValueError) as e:
            return bad(str(e))

    # --- curiosidade / ciclos (Fase 3) ---
    @app.get("/api/admin/topics")
    @admin_only
    def topics():
        with db.conn() as c:
            return jsonify(curiosity.list_topics(c))

    @app.post("/api/admin/topics")
    @admin_only
    def add_topic():
        d = request.get_json(silent=True) or {}
        with db.conn() as c:
            tid = curiosity.add_topic(c, d.get("title", ""), "admin", d.get("priority", 5))
        return (jsonify(id=tid), 201) if tid else bad("tema inválido, repetido ou limite atingido")

    @app.patch("/api/admin/topics/<int:tid>")
    @admin_only
    def topic_status(tid):
        with db.conn() as c:
            ok = curiosity.set_topic_status(c, tid, (request.get_json(silent=True) or {}).get("status", ""))
        return jsonify(ok=True) if ok else bad("tema ou status inválido")

    @app.get("/api/admin/investigations")
    @admin_only
    def investigations():
        with db.conn() as c:
            return jsonify(curiosity.journal(c, 50))

    @app.post("/api/admin/cycle")
    @admin_only
    def cycle():
        return jsonify(curiosity.run_cycle(svc, actor="admin-trigger"))

    # --- pensamento: reflexão, propostas, objetivos, autoconhecimento ---
    @app.post("/api/admin/reflect")
    @admin_only
    def reflect():
        return jsonify(run_reflection(svc, actor="admin-trigger", force=request.args.get("force") == "1"))

    @app.get("/api/admin/proposals")
    @admin_only
    def list_proposals():
        with db.conn() as c:
            return jsonify(proposals.list_(c, request.args.get("status")))

    def _decide(pid, approve):
        d = request.get_json(silent=True) or {}
        try:
            return jsonify(proposals.decide(svc, pid, approve, d.get("note", "")))
        except ValueError as e:
            return bad(str(e))

    @app.post("/api/admin/proposals/<int:pid>/approve")
    @admin_only
    def approve_proposal(pid):
        return _decide(pid, True)

    @app.post("/api/admin/proposals/<int:pid>/reject")
    @admin_only
    def reject_proposal(pid):
        return _decide(pid, False)

    @app.get("/api/admin/goals")
    @admin_only
    def list_goals():
        with db.conn() as c:
            return jsonify(goals.list_goals(c))

    @app.patch("/api/admin/goals/<int:gid>")
    @admin_only
    def patch_goal(gid):
        with db.conn() as c:
            ok = goals.set_status(c, gid, (request.get_json(silent=True) or {}).get("status", ""))
        return jsonify(ok=True) if ok else bad("objetivo ou status inválido")

    @app.get("/api/admin/thoughts")
    @admin_only
    def thoughts():
        with db.conn() as c:
            rows = c.execute("SELECT id, content, created_at FROM thoughts ORDER BY id DESC LIMIT 50").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/api/admin/autobiography")
    @admin_only
    def autobiography():
        with db.conn() as c:
            rows = c.execute("SELECT id, text, created_at FROM autobiography ORDER BY id DESC LIMIT 30").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/api/admin/self")
    @admin_only
    def self_model():
        return jsonify(description=selfmodel.describe(svc), indicators=selfmodel.indicators(svc))

    # --- avatar (Fase 5) ---
    @app.post("/api/admin/avatar")
    @admin_only
    def avatar_change():
        d = request.get_json(silent=True) or {}
        try:
            with db.conn() as c:
                return jsonify(avatar.evolve_look(c, d.get("changes"), d.get("reason", "")))
        except ValueError as e:
            return bad(str(e))

    # --- backup / recuperação / manutenção (Fase 6) ---
    @app.get("/api/admin/backups")
    @admin_only
    def backups():
        return jsonify(backup.list_backups(cfg.backup_dir))

    @app.post("/api/admin/backups")
    @admin_only
    def make_backup():
        p = backup.snapshot(db.path, cfg.backup_dir, keep=cfg.backup_keep, tag="manual")
        with db.conn() as c:
            audit.log(c, "admin", "backup_created", {"name": p.name})
        return jsonify(name=p.name), 201

    @app.get("/api/admin/backup/export")
    @admin_only
    def export_backup():
        with db.conn() as c:
            audit.log(c, "admin", "backup_exported")
        return Response(backup.export_bytes(db.path), mimetype="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename=alice-export.db"})

    @app.post("/api/admin/backup/restore")
    @admin_only
    def restore_backup():
        try:
            up = request.files.get("file")
            if up:
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as t:
                    up.save(t.name)
                try:
                    safety = backup.restore(db.path, t.name, cfg.backup_dir)
                finally:
                    os.unlink(t.name)
            else:
                safety = backup.restore_named(db.path, cfg.backup_dir, (request.get_json(silent=True) or {}).get("name", ""))
        except ValueError as e:
            return bad(str(e))
        db.init()
        with db.conn() as c:
            audit.log(c, "admin", "backup_restored", {"safety_backup": safety.name}, level="warning")
        return jsonify(ok=True, safety_backup=safety.name)

    @app.post("/api/admin/outreach/run")
    @admin_only
    def outreach_run():
        return jsonify(run_outreach(svc, actor="admin-trigger"))

    @app.get("/api/admin/outreach")
    @admin_only
    def outreach_list():
        with db.conn() as c:
            rows = [dict(r) for r in c.execute("SELECT id, visitor_id, kind, text, created_at, delivered_at FROM proactive_messages ORDER BY id DESC LIMIT 50")]
        return jsonify(rows)

    @app.post("/api/admin/tick")
    @admin_only
    def admin_tick():  # para cron externo (também acorda o serviço no plano gratuito)
        return jsonify(ran=tick(svc, cfg))

    @app.get("/api/admin/remote")
    @admin_only
    def remote_status():
        return jsonify(remote.status())

    @app.post("/api/admin/remote/sync")
    @admin_only
    def remote_sync():
        return jsonify(remote.sync(force=True))

    @app.post("/api/admin/maintenance")
    @admin_only
    def maintenance():
        return jsonify(run_maintenance(svc, cfg))

    return app
