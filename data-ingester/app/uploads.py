import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from .config import Settings
from .store import Store
from .util import ensure_space, now_iso, safe_filename


class UploadConflict(Exception):
    def __init__(self, message: str, offset: int):
        super().__init__(message)
        self.offset = offset


class UploadNotFound(Exception):
    pass


class UploadTooLarge(Exception):
    pass


class UploadManager:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.folder = settings.work_dir / "uploads"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.items: dict[str, dict] = {doc["id"]: doc for doc in store.all("uploads")}
        self.locks: dict[str, asyncio.Lock] = {}
        for doc in self.items.values():
            self._refresh(doc)

    def path(self, doc: dict) -> Path:
        return self.folder / f"{doc['id']}.part"

    def _refresh(self, doc: dict) -> dict:
        path = self.path(doc)
        received = path.stat().st_size if path.exists() else 0
        doc["received"] = received
        if doc.get("jobId"):
            return doc
        doc["status"] = "complete" if received == doc["size"] else "uploading"
        return doc

    def _save(self, doc: dict) -> None:
        doc["updatedAt"] = now_iso()
        doc["touched"] = time.time()
        self.store.put("uploads", doc)

    def public(self, doc: dict) -> dict:
        return {key: doc[key] for key in ("id", "filename", "size", "received", "status", "createdAt", "updatedAt", "jobId") if key in doc}

    def list(self) -> list[dict]:
        return [self.public(self._refresh(doc)) for doc in sorted(self.items.values(), key=lambda item: item["createdAt"])]

    def get(self, upload_id: str) -> dict:
        doc = self.items.get(upload_id)
        if not doc:
            raise UploadNotFound(upload_id)
        return self._refresh(doc)

    def create(self, filename: str, size: int, fingerprint: str, user: dict) -> dict:
        if size <= 0:
            raise ValueError("The file is empty.")
        if size > self.settings.max_upload_bytes:
            raise UploadTooLarge(f"The file is larger than the {self.settings.max_upload_bytes} byte limit.")
        for doc in self.items.values():
            if doc.get("fingerprint") == fingerprint and doc["size"] == size and not doc.get("jobId"):
                self._refresh(doc)
                self._save(doc)
                return doc
        reserved = sum(doc["size"] - doc.get("received", 0) for doc in self.items.values() if not doc.get("jobId"))
        ensure_space(self.settings, self.folder, size + reserved, "store this upload")
        doc = {
            "id": uuid.uuid4().hex,
            "filename": safe_filename(filename, "upload.zip"),
            "size": size,
            "fingerprint": fingerprint[:512],
            "received": 0,
            "status": "uploading",
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "jobId": None,
            "owner": {"id": user.get("id"), "role": user.get("role")},
        }
        self.path(doc).touch()
        self.items[doc["id"]] = doc
        self._save(doc)
        return doc

    async def write(self, upload_id: str, offset: int, stream: AsyncIterator[bytes]) -> dict:
        doc = self.get(upload_id)
        if doc.get("jobId"):
            raise UploadConflict("This upload is already attached to an ingestion job.", doc["received"])
        lock = self.locks.setdefault(upload_id, asyncio.Lock())
        if lock.locked():
            raise UploadConflict("Another chunk for this upload is still being written.", doc["received"])
        async with lock:
            self._refresh(doc)
            if offset != doc["received"]:
                raise UploadConflict("The upload offset does not match the stored size.", doc["received"])
            path = self.path(doc)
            written = doc["received"]
            try:
                with open(path, "r+b") as handle:
                    handle.seek(offset)
                    handle.truncate()
                    async for chunk in stream:
                        if not chunk:
                            continue
                        room = doc["size"] - written
                        if len(chunk) > room:
                            handle.write(chunk[:room])
                            written += room
                            raise UploadTooLarge("More data was sent than the declared file size.")
                        handle.write(chunk)
                        written += len(chunk)
            finally:
                self._refresh(doc)
                self._save(doc)
            return doc

    def attach(self, upload_id: str, job_id: str) -> dict:
        doc = self.get(upload_id)
        if doc.get("jobId") and doc["jobId"] != job_id:
            raise UploadConflict("This upload is already attached to another ingestion job.", doc["received"])
        if doc["status"] != "complete" and not doc.get("jobId"):
            raise UploadConflict(f"{doc['filename']} is not fully uploaded yet.", doc["received"])
        doc["jobId"] = job_id
        doc["status"] = "attached"
        self._save(doc)
        return doc

    def release(self, upload_id: str) -> None:
        doc = self.items.pop(upload_id, None)
        if doc:
            self.path(doc).unlink(missing_ok=True)
        self.store.delete("uploads", upload_id)
        self.locks.pop(upload_id, None)

    def delete(self, upload_id: str) -> None:
        doc = self.get(upload_id)
        if doc.get("jobId"):
            raise UploadConflict("This upload belongs to an ingestion job; remove the job instead.", doc["received"])
        self.release(upload_id)

    def expire(self) -> int:
        limit = time.time() - self.settings.upload_ttl_hours * 3600
        stale = [doc["id"] for doc in self.items.values() if not doc.get("jobId") and doc.get("touched", 0) < limit]
        for upload_id in stale:
            self.release(upload_id)
        return len(stale)
