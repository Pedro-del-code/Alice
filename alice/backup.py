"""Fase 6 — Cópias de segurança e recuperação (SQLite backup API: consistente mesmo com o banco em uso)."""
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

NAME_RE = re.compile(r"^alice-\d{8}-\d{6}(-[a-z]+)?\.db$")
REQUIRED = {"identity", "memories", "audit_log", "personality_versions", "visitors"}


def _copy(src_path: str, dst_path: str) -> None:
    src, dst = sqlite3.connect(src_path, timeout=15), sqlite3.connect(dst_path, timeout=15)
    try:
        src.backup(dst)
    finally:
        src.close(); dst.close()


def snapshot(db_path: str, backup_dir: str, keep: int = 7, tag: str = "") -> Path:
    d = Path(backup_dir); d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = d / f"alice-{stamp}{('-' + tag) if tag else ''}.db"
    _copy(db_path, str(out))
    for old in sorted(d.glob("alice-*.db"))[:-max(keep, 1)] if keep else []:
        old.unlink(missing_ok=True)
    return out


def list_backups(backup_dir: str) -> list[dict]:
    d = Path(backup_dir)
    return [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(d.glob("alice-*.db"), reverse=True)] if d.exists() else []


def export_bytes(db_path: str) -> bytes:
    with tempfile.TemporaryDirectory() as t:
        out = str(Path(t) / "export.db")
        _copy(db_path, out)
        return Path(out).read_bytes()


def validate(path: str) -> None:
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("arquivo corrompido")
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            c.close()
    except sqlite3.DatabaseError:
        raise ValueError("não é um banco SQLite válido") from None
    if not REQUIRED <= tables:
        raise ValueError("banco não parece ser da Alice (tabelas ausentes)")


def restore(db_path: str, source_path: str, backup_dir: str) -> Path:
    """Valida o arquivo, guarda um snapshot de segurança do estado atual e só então sobrescreve."""
    validate(source_path)
    safety = snapshot(db_path, backup_dir, keep=0, tag="prerestore")  # keep=0 → não rotaciona
    _copy(source_path, db_path)
    return safety


def restore_named(db_path: str, backup_dir: str, name: str) -> Path:
    if not NAME_RE.match(name):
        raise ValueError("nome de backup inválido")
    p = Path(backup_dir) / name
    if not p.is_file():
        raise ValueError("backup não encontrado")
    return restore(db_path, str(p), backup_dir)
