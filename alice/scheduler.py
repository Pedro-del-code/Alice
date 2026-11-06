"""Agendador em segundo plano (1 thread). No Render use 1 worker do gunicorn (veja render.yaml)."""
import threading
import time

from . import audit, backup, memory


def run_maintenance(svc, cfg) -> dict:
    """Backup + retenção de visitantes inativos + verificação da cadeia de auditoria."""
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
    return out


class Scheduler(threading.Thread):
    def __init__(self, svc, cfg):
        super().__init__(daemon=True, name="alice-scheduler")
        self.svc, self.cfg, self.stop_event = svc, cfg, threading.Event()

    def run(self) -> None:
        from .curiosity import run_cycle
        from .reflection import run_reflection
        last_cycle = last_maint = last_refl = 0.0
        self.stop_event.wait(60)  # dá tempo para o serviço subir
        while not self.stop_event.is_set():
            t = time.monotonic()
            try:
                if self.cfg.autonomy and t - last_cycle >= self.cfg.cycle_interval_min * 60:
                    last_cycle = t
                    run_cycle(self.svc)
                if self.cfg.autonomy and t - last_refl >= 6 * 3600:  # a função só reflete 1x por dia
                    last_refl = t
                    run_reflection(self.svc)
                if t - last_maint >= 24 * 3600:
                    last_maint = t
                    run_maintenance(self.svc, self.cfg)
            except Exception as e:  # noqa: BLE001
                with self.svc.db.conn() as c:
                    audit.log(c, "system", "scheduler_error", {"error": type(e).__name__}, level="error")
            self.stop_event.wait(60)


def start_scheduler(svc, cfg) -> Scheduler:
    s = Scheduler(svc, cfg)
    s.start()
    return s
