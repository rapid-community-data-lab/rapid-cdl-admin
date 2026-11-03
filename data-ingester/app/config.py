import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in _env(name, default).split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    jwt_secret: str
    allowed_roles: tuple[str, ...]
    lab_api_url: str
    lab_api_roles: tuple[str, ...]
    ocfl_root: Path
    work_dir: Path
    state_dir: Path
    min_free_bytes: int
    min_free_percent: float
    download_concurrency: int
    download_retries: int
    retry_backoff_max: float
    connect_timeout: float
    read_timeout: float
    upload_chunk_size: int
    max_upload_bytes: int
    upload_ttl_hours: int
    keep_archives: bool
    index_timeout: int
    index_poll_interval: float
    index_settle_seconds: int
    allowed_download_hosts: tuple[str, ...]
    allow_private_urls: bool
    allow_http: bool
    cors_origins: tuple[str, ...]
    max_urls: int


def load_settings() -> Settings:
    work_dir = Path(_env("INGEST_WORK_DIR", "/data/ocfl/ingest"))
    return Settings(
        jwt_secret=_env("API_AUTH_JWT_SECRET"),
        allowed_roles=_list("ALLOWED_ROLES", "INDEX_ADMIN,ROLES_ADMIN,ADMIN,SUPER_ADMIN"),
        lab_api_url=_env("LAB_API_URL", "http://rapid-community-data-lab-api:8080").rstrip("/"),
        lab_api_roles=_list("LAB_API_ADMIN_ROLES", "ADMIN,INDEX_ADMIN"),
        ocfl_root=Path(_env("OCFL_PATH", "/data/ocfl/data")),
        work_dir=work_dir,
        state_dir=Path(_env("INGEST_STATE_DIR", str(work_dir))),
        min_free_bytes=_int("MIN_FREE_BYTES", 4 * 1024 ** 3),
        min_free_percent=_float("MIN_FREE_PERCENT", 15.0),
        download_concurrency=max(1, _int("DOWNLOAD_CONCURRENCY", 2)),
        download_retries=max(0, _int("DOWNLOAD_RETRIES", 30)),
        retry_backoff_max=_float("RETRY_BACKOFF_MAX", 60.0),
        connect_timeout=_float("CONNECT_TIMEOUT", 30.0),
        read_timeout=_float("READ_TIMEOUT", 120.0),
        upload_chunk_size=_int("UPLOAD_CHUNK_SIZE", 8 * 1024 ** 2),
        max_upload_bytes=_int("MAX_UPLOAD_BYTES", 50 * 1024 ** 3),
        upload_ttl_hours=_int("UPLOAD_TTL_HOURS", 72),
        keep_archives=_bool("KEEP_ARCHIVES", False),
        index_timeout=_int("INDEX_TIMEOUT", 3600),
        index_poll_interval=_float("INDEX_POLL_INTERVAL", 3.0),
        index_settle_seconds=_int("INDEX_SETTLE_SECONDS", 45),
        allowed_download_hosts=_list("ALLOWED_DOWNLOAD_HOSTS", ""),
        allow_private_urls=_bool("ALLOW_PRIVATE_URLS", False),
        allow_http=_bool("ALLOW_HTTP", False),
        cors_origins=_list("CORS_ORIGINS", ""),
        max_urls=_int("MAX_URLS", 100),
    )
