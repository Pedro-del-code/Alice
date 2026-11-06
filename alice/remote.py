"""Persistência no plano GRATUITO do Render (sem disco): o banco SQLite é copiado para um armazenamento
externo compatível com S3 (Supabase Storage, Backblaze B2, Cloudflare R2, MinIO, AWS S3...) e restaurado sozinho
quando o serviço acorda/reinicia. Assinatura AWS SigV4 em Python puro (sem boto3). Criptografia opcional (Fernet).

Limites honestos: entre dois envios (padrão 5 min) pode haver perda; se o provedor estiver fora do ar no boot
e não houver banco local, o serviço NÃO inicia (para nunca sobrescrever o backup remoto com um banco vazio)."""
import base64
import functools
import hashlib
import hmac
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import backup

SQLITE_MAGIC = b"SQLite format 3\x00"


class RemoteError(Exception):
    """Erro do armazenamento remoto. Nunca contém chaves."""


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign(method, host, path, query, headers, payload_hash, amzdate, region, service, access_key, secret_key):
    """AWS Signature V4. `headers`: cabeçalhos a assinar (nomes minúsculos, incluindo host e x-amz-date).
    Retorna (valor do Authorization, assinatura hex)."""
    names = sorted(headers)
    canon_headers = "".join(f"{n}:{' '.join(str(headers[n]).split())}\n" for n in names)
    signed = ";".join(names)
    q = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}" for k, v in sorted(query))
    canonical = "\n".join([method, path, q, canon_headers, signed, payload_hash])
    scope = f"{amzdate[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope, _sha(canonical.encode())])
    k = _hmac(("AWS4" + secret_key).encode(), amzdate[:8])
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}", sig


class S3Store:
    def __init__(self, endpoint, bucket, region, access_key, secret_key, prefix="alice", timeout=30):
        self.endpoint, self.bucket, self.region = endpoint.rstrip("/"), bucket, region
        self._ak, self._sk, self.prefix, self.timeout = access_key, secret_key, prefix.strip("/"), timeout
        self.host = urllib.parse.urlparse(self.endpoint).netloc

    def _request(self, method: str, key: str, body: bytes = b"") -> tuple[int, bytes]:
        path = "/" + urllib.parse.quote(f"{self.bucket}/{self.prefix}/{key}", safe="/-_.~")
        amzdate = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ph = _sha(body)
        auth, _ = sign(method, self.host, path, [], {"host": self.host, "x-amz-content-sha256": ph, "x-amz-date": amzdate},
                       ph, amzdate, self.region, "s3", self._ak, self._sk)
        req = urllib.request.Request(self.endpoint + path, data=body if method == "PUT" else None, method=method, headers={
            "x-amz-content-sha256": ph, "x-amz-date": amzdate, "Authorization": auth, "User-Agent": "alice-prototype/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, b""
            raise RemoteError(f"armazenamento remoto retornou HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RemoteError(f"falha de rede com o armazenamento remoto ({type(e).__name__})") from None

    def put(self, key: str, data: bytes) -> None:
        self._request("PUT", key, data)

    def get(self, key: str) -> bytes | None:
        status, data = self._request("GET", key)
        return None if status == 404 else data


@functools.lru_cache(maxsize=4)
def _fernet(passphrase: str):
    from cryptography.fernet import Fernet
    k = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), b"alice-backup-v1", 200_000, 32)
    return Fernet(base64.urlsafe_b64encode(k))


def seal(data: bytes, passphrase: str) -> bytes:
    if not passphrase:
        return data
    try:
        return _fernet(passphrase).encrypt(data)
    except ImportError:
        raise RemoteError("ALICE_BACKUP_PASSPHRASE definida, mas o pacote 'cryptography' não está instalado") from None


def unseal(blob: bytes, passphrase: str) -> bytes:
    if blob.startswith(SQLITE_MAGIC):
        return blob  # backup antigo sem criptografia
    if not passphrase:
        raise RemoteError("o backup remoto está criptografado, mas ALICE_BACKUP_PASSPHRASE não foi definida")
    try:
        return _fernet(passphrase).decrypt(blob)
    except ImportError:
        raise RemoteError("pacote 'cryptography' ausente") from None
    except Exception:  # noqa: BLE001 — InvalidToken
        raise RemoteError("senha de backup incorreta ou arquivo corrompido") from None


class RemoteSync:
    def __init__(self, cfg, db_path: str):
        self.cfg, self.db_path = cfg, str(db_path)
        self.enabled = bool(cfg.remote_enabled)
        self.store = S3Store(cfg.s3_endpoint, cfg.s3_bucket, cfg.s3_region, cfg.s3_access_key,
                             cfg.s3_secret_key, cfg.s3_prefix) if self.enabled else None
        self.last_mtime = 0
        self.last_ok: str | None = None
        self.last_error: str | None = None
        self.last_daily: str | None = None
        self.restored = "disabled"
        self._lock = threading.Lock()

    def _mtime(self) -> int:
        try:
            return os.stat(self.db_path).st_mtime_ns
        except OSError:
            return 0

    def restore_if_missing(self) -> str:
        """Chamar ANTES de abrir o banco. 'restored' | 'local_exists' | 'no_remote' | 'disabled'."""
        if not self.enabled:
            return "disabled"
        p = Path(self.db_path)
        if p.exists() and p.stat().st_size > 0:
            self.restored = "local_exists"
            return self.restored
        blob, err = None, None
        for attempt in range(3):
            try:
                blob, err = self.store.get("latest.db"), None
                break
            except RemoteError as e:
                err = e
                time.sleep(1 + attempt)
        if err:  # sem banco local e sem acesso ao remoto: não inicia (evita sobrescrever o backup com um banco vazio)
            raise err
        if blob is None:
            self.restored = "no_remote"
            return self.restored
        data = unseal(blob, self.cfg.backup_passphrase)
        p.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as t:
            tmp = str(Path(t) / "remote.db")
            Path(tmp).write_bytes(data)
            try:
                backup.validate(tmp)
            except ValueError as e:
                raise RemoteError(f"backup remoto inválido: {e}") from None
            backup._copy(tmp, self.db_path)
        self.last_mtime = self._mtime()
        self.restored = "restored"
        return self.restored

    def sync(self, force: bool = False) -> dict:
        if not self.enabled:
            return {"status": "disabled"}
        with self._lock:
            mt = self._mtime()
            if not force and mt == self.last_mtime:
                return {"status": "unchanged"}
            try:
                with tempfile.TemporaryDirectory() as t:
                    tmp = str(Path(t) / "snap.db")
                    backup._copy(self.db_path, tmp)
                    blob = seal(Path(tmp).read_bytes(), self.cfg.backup_passphrase)
                self.store.put("latest.db", blob)
                day = datetime.now(timezone.utc).strftime("%Y%m%d")
                if self.last_daily != day:
                    self.store.put(f"daily-{day}.db", blob)
                    self.last_daily = day
                self.last_mtime, self.last_ok, self.last_error = mt, datetime.now(timezone.utc).isoformat(timespec="seconds"), None
                return {"status": "uploaded", "bytes": len(blob)}
            except (RemoteError, backup.sqlite3.Error, OSError) as e:
                self.last_error = type(e).__name__ if not isinstance(e, RemoteError) else str(e)
                return {"status": "error", "error": self.last_error}

    def status(self) -> dict:
        return {"enabled": self.enabled, "encrypted": bool(self.cfg.backup_passphrase), "startup": self.restored,
                "last_ok": self.last_ok, "last_error": self.last_error, "interval_min": self.cfg.remote_sync_min}
