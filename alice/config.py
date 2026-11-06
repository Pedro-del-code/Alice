import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Carrega .env simples (KEY=VALUE) sem sobrescrever variáveis já definidas."""
    path = path or BASE_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _b(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "on", "true", "yes", "sim")


@dataclass
class Config:
    db_path: str = ""
    backup_dir: str = ""
    constitution_path: str = str(BASE_DIR / "alice" / "data" / "constitution.md")
    groq_api_key: str = field(default="", repr=False)  # repr=False: nunca vaza em logs
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    admin_token: str = field(default="", repr=False)
    admin_password: str = field(default="", repr=False)   # login do painel; vem do ambiente, nunca do código
    secret_key: str = field(default="", repr=False)       # assina o cookie de sessão
    daily_token_budget: int = 150_000
    rpm_limit: int = 25
    ip_rpm_limit: int = 12            # por IP, só em memória (proteção de cota; IP não é identidade)
    max_message_chars: int = 2000
    max_output_tokens: int = 600
    history_turns: int = 10
    backup_keep: int = 7
    deliberate: bool = True           # pensamento interno antes de responder (custa ~100-200 tokens/resposta)
    thought_retention_days: int = 7   # pensamentos ligados a visitantes são apagados rápido (minimização)
    tz_offset_hours: int = -3         # fuso do administrador (ritmo do dia)
    embed_url: str = ""               # opcional: provedor de embeddings OpenAI-compatível
    embed_key: str = field(default="", repr=False)
    embed_model: str = "text-embedding-3-small"
    retention_days: int = 90          # visitantes inativos são apagados (Art. 30)
    # Autonomia (Fase 3) — tudo limitado e auditado
    scheduler: bool = False
    autonomy: bool = False
    cycle_interval_min: int = 180
    cycles_per_day: int = 6
    cycle_usage_ceiling: float = 0.6  # ciclos só rodam enquanto o uso do dia < 60% (o resto é dos visitantes)
    behind_proxy: bool = False        # True no Render (TLS termina no proxy)

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        e = os.environ.get
        db = e("ALICE_DB_PATH", "data/alice.db")
        if not os.path.isabs(db):
            db = str(BASE_DIR / db)
        return cls(
            db_path=db,
            backup_dir=e("ALICE_BACKUP_DIR", ""),
            groq_api_key=e("GROQ_API_KEY", ""),
            groq_model=e("GROQ_MODEL", cls.groq_model),
            groq_base_url=e("GROQ_BASE_URL", cls.groq_base_url),
            admin_token=e("ADMIN_TOKEN", ""),
            admin_password=e("ADMIN_PASSWORD", ""),
            secret_key=e("ALICE_SECRET_KEY", ""),
            daily_token_budget=int(e("ALICE_DAILY_TOKEN_BUDGET", 150_000)),
            rpm_limit=int(e("ALICE_RPM_LIMIT", 25)),
            ip_rpm_limit=int(e("ALICE_IP_RPM_LIMIT", 12)),
            retention_days=int(e("ALICE_RETENTION_DAYS", 90)),
            tz_offset_hours=int(e("ALICE_TZ_OFFSET_HOURS", -3)),
            embed_url=e("ALICE_EMBED_URL", ""),
            embed_key=e("ALICE_EMBED_KEY", ""),
            embed_model=e("ALICE_EMBED_MODEL", "text-embedding-3-small"),
            deliberate=_b(e("ALICE_DELIBERATE", "on")),
            thought_retention_days=int(e("ALICE_THOUGHT_RETENTION_DAYS", 7)),
            backup_keep=int(e("ALICE_BACKUP_KEEP", 7)),
            scheduler=_b(e("ALICE_SCHEDULER")),
            autonomy=_b(e("ALICE_AUTONOMY")),
            cycle_interval_min=int(e("ALICE_CYCLE_INTERVAL_MIN", 180)),
            cycles_per_day=int(e("ALICE_CYCLES_PER_DAY", 6)),
            cycle_usage_ceiling=float(e("ALICE_CYCLE_USAGE_CEILING", 0.6)),
            behind_proxy=_b(e("ALICE_BEHIND_PROXY")),
        )


def password_warning(pw: str) -> str | None:
    """Aviso (não bloqueio) para senhas fracas. A decisão final é do administrador."""
    if not pw:
        return None
    if len(pw) < 12 or pw.isalpha():
        return ("ADMIN_PASSWORD é curta/previsível (nome ou palavra simples é fácil de adivinhar). "
                "Prefira 12+ caracteres com números e símbolos.")
    return None
