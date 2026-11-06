"""Agendamento PERSISTENTE: os horários da última execução ficam no banco, então reinícios (comuns no plano gratuito do
Render) não disparam ciclos em sequência. `tick()` executa só o que está vencido e pode ser chamado por uma thread
ou por um cron externo (POST /api/admin/tick), que também acorda o serviço."""
import threading
import time

from . import audit, backup, memory


def run_maintenance(svc, cfg) -> dict:
    """Backup + retenção + esquecimento natural + verificação da auditoria (+ envio ao armazenamento remoto)."""
    out = {}
    try:
        b = backup.snapshot(svc.db.path, cfg.backup_dir, keep=cfg.backup_keep)
        out["backup"] = b.name
    except Exception as e:  # noqa: BLE001 — a manutenção não pode derrubar o serviço
        out["backup_error"] = type(e).__name__
    out["purged_visitors"] = svc.purge_inactive(cfg.retention_days)
    out["purged_thoughts"] = svc.purge_thoughts(cfg.thought_retention_days)
    with svc.db.conn() as c:
        out["archived_memories"] = memory.consolidate(c, actor="system")  # esquecimento natural (Art. 51)
    with svc.db.conn() as c:
        v = audit.verify(c)
        audit.log(c, "system", "maintenance", {**out, "audit_ok": v["ok"]}, level="info" if v["ok"] else "error")
    out["audit_ok"] = v["ok"]
    remote = getattr(svc, "remote", None)
    if remote is not None and remote.enabled:
        out["remote"] = remote.sync()["status"]
    return out


def _due(c, key: str, every_s: float) -> bool:
    r = c.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
    return time.time() - (float(r["value"]) if r else 0.0) >= every_s


def _mark(c, key: str) -> None:
    c.execute("INSERT INTO system_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
              (key, str(time.time())))


def tick(svc, cfg) -> dict:
    """Roda o que estiver vencido: ciclo de curiosidade, reflexão (1x/dia, checada a cada 6 h) e manutenção (24 h)."""
    from .curiosity import run_cycle
    from .outreach import run_outreach
    from .reflection import run_reflection
    with svc.db.conn() as c:
        d_cycle = cfg.autonomy and _due(c, "ts_cycle", cfg.cycle_interval_min * 60)
        d_refl = cfg.autonomy and _due(c, "ts_refl", 6 * 3600)
        d_maint = _due(c, "ts_maint", 24 * 3600)
        d_out = cfg.autonomy and cfg.proactive and _due(c, "ts_outreach", 600)  # iniciativa faz parte da autonomia
        for flag, key in ((d_cycle, "ts_cycle"), (d_refl, "ts_refl"), (d_maint, "ts_maint"), (d_out, "ts_outreach")):
            if flag:
                _mark(c, key)  # marca antes de rodar: se travar, não entra em loop
    ran = {}
    if d_cycle:
        ran["cycle"] = run_cycle(svc)
    if d_refl:
        ran["reflection"] = run_reflection(svc)
    if d_out:
        ran["outreach"] = run_outreach(svc)
    if d_maint:
        ran["maintenance"] = run_maintenance(svc, cfg)
    return ran


class Scheduler(threading.Thread):
    def __init__(self, svc, cfg, remote=None):
        super().__init__(daemon=True, name="alice-scheduler")
        self.svc, self.cfg, self.remote, self.stop_event = svc, cfg, remote, threading.Event()

    def run(self) -> None:
        last_sync = time.monotonic()
        self.stop_event.wait(60)  # dá tempo para o serviço subir
        while not self.stop_event.is_set():
            try:
                if self.cfg.scheduler:
                    tick(self.svc, self.cfg)
                if self.remote is not None and self.remote.enabled and time.monotonic() - last_sync >= self.cfg.remote_sync_min * 60:
                    last_sync = time.monotonic()
                    self.remote.sync()
            except Exception as e:  # noqa: BLE001
                with self.svc.db.conn() as c:
                    audit.log(c, "system", "scheduler_error", {"error": type(e).__name__}, level="error")
            self.stop_event.wait(60)


def start_scheduler(svc, cfg, remote=None) -> Scheduler:
    s = Scheduler(svc, cfg, remote)
    s.start()
    return s
