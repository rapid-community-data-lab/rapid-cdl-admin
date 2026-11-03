import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings


class InsufficientSpace(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def human_bytes(value: float | int | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def safe_filename(name: str | None, fallback: str = "file") -> str:
    if not name:
        return fallback
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)
    if base in ("", ".", ".."):
        return fallback
    return base[:200]


def disk_status(settings: Settings, path: Path) -> dict:
    target = path if path.exists() else path.parent
    while not target.exists() and target != target.parent:
        target = target.parent
    usage = shutil.disk_usage(target)
    reserve = int(max(settings.min_free_bytes, usage.total * settings.min_free_percent / 100))
    return {"total": usage.total, "free": usage.free, "reserve": reserve, "usable": max(0, usage.free - reserve)}


def ensure_space(settings: Settings, path: Path, needed: int, purpose: str) -> None:
    status = disk_status(settings, path)
    if needed > status["usable"]:
        raise InsufficientSpace(
            f"Not enough disk space to {purpose}: {human_bytes(needed)} needed, "
            f"{human_bytes(status['free'])} free and {human_bytes(status['reserve'])} must stay free."
        )


def natural_key(value: str) -> list:
    return [int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", value or "")]
