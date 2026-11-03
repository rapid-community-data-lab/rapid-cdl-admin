import asyncio
import re
import time
from collections.abc import Callable
from urllib.parse import quote

import httpx

from .auth import Auth
from .config import Settings

SWEEP_LIMIT = 60


class IndexingError(Exception):
    pass


def crate_pattern(crate_id: str) -> str:
    return re.sub(r"([\\^$.|?*+()\[\]{}])", r"\\\1", crate_id) + "(?:/|$)"


class LabApi:
    def __init__(self, settings: Settings, auth: Auth):
        self.settings = settings
        self.auth = auth
        self.client = httpx.AsyncClient(base_url=settings.lab_api_url, timeout=httpx.Timeout(60.0, connect=10.0))

    async def close(self) -> None:
        await self.client.aclose()

    async def version(self) -> dict | None:
        try:
            response = await self.client.get("/version", timeout=5.0)
            return response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def members(self, parent_id: str) -> int | None:
        response = await self.client.get("/entities", params={"memberOf": parent_id, "limit": 1})
        if response.status_code != 200:
            return None
        try:
            return int(response.json().get("total"))
        except (ValueError, TypeError):
            return None

    async def snapshot(self, crate_id: str) -> dict:
        root = await self.client.get(f"/entity/{quote(crate_id, safe='')}")
        return {"root": root.status_code == 200, "members": await self.members(crate_id)}

    async def trigger(self, crate_id: str, force: bool = True) -> dict:
        path = f"/admin/index/{quote(crate_pattern(crate_id), safe='')}"
        params = {"force": "true"} if force else {}
        last = None
        for role in self.settings.lab_api_roles:
            token = self.auth.service_token(role)
            for attempt in range(6):
                try:
                    response = await self.client.post(path, params=params, headers={"Authorization": f"Bearer {token}"})
                except httpx.HTTPError as exc:
                    last = f"The portal API is unreachable: {exc}"
                    await asyncio.sleep(min(30, 2 ** attempt))
                    continue
                if response.status_code in (200, 202):
                    return {"status": response.status_code, "role": role}
                if response.status_code == 401:
                    last = "The portal API rejected the ingester token (check API_AUTH_JWT_SECRET on both services)."
                    break
                if response.status_code == 409:
                    last = "The portal API is busy indexing or deleting; retrying."
                    await asyncio.sleep(10)
                    continue
                if response.status_code >= 500:
                    last = f"The portal API answered HTTP {response.status_code}."
                    await asyncio.sleep(min(30, 2 ** attempt))
                    continue
                raise IndexingError(f"The portal API answered HTTP {response.status_code}: {response.text[:200]}")
        raise IndexingError(last or "Indexing could not be started.")

    async def _sweep(self, parents: dict[str, int], verified: set[str]) -> bool:
        checked = 0
        for parent, expected in parents.items():
            if parent in verified:
                continue
            if checked >= SWEEP_LIMIT:
                return False
            checked += 1
            total = await self.members(parent)
            if total is None:
                verified.add(parent)
                continue
            if total < expected:
                return False
            verified.add(parent)
        return True

    async def wait(
        self,
        crate_id: str,
        expected: int,
        existed: bool,
        on_progress: Callable[[dict], None],
        cancelled: Callable[[], str | None],
        interrupted: type[Exception],
        parents: dict[str, int] | None = None,
    ) -> dict:
        checks = {key: value for key, value in (parents or {}).items() if key != crate_id}
        started = time.monotonic()
        saw_reset = not existed
        verified: set[str] = set()
        stable_since = None
        last = None
        while True:
            reason = cancelled()
            if reason:
                raise interrupted(reason)
            elapsed = time.monotonic() - started
            try:
                snap = await self.snapshot(crate_id)
                members = snap.get("members") or 0
                ready = snap["root"] and members >= expected
                if not ready:
                    verified.clear()
                complete = ready and await self._sweep(checks, verified)
                if not complete:
                    saw_reset = True
            except httpx.HTTPError:
                snap, members, complete = {"root": False}, 0, False
            on_progress(
                {
                    "members": members,
                    "expected": expected,
                    "root": snap["root"],
                    "verified": len(verified),
                    "parents": len(checks),
                    "elapsed": int(elapsed),
                }
            )
            if complete and (saw_reset or elapsed >= self.settings.index_settle_seconds):
                return {"status": "indexed", "members": members, "expected": expected}
            current = (snap["root"], members, len(verified))
            if current != last:
                stable_since = time.monotonic()
                last = current
            if (
                snap["root"]
                and members > 0
                and saw_reset
                and stable_since is not None
                and time.monotonic() - stable_since >= max(120, self.settings.index_settle_seconds * 3)
            ):
                return {"status": "settled", "members": members, "expected": expected}
            if elapsed >= self.settings.index_timeout:
                return {"status": "timeout", "members": members, "expected": expected}
            await asyncio.sleep(self.settings.index_poll_interval)
