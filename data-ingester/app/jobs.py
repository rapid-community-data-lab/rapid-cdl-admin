import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

import httpx

from . import assemble
from . import crate as crates
from .auth import Auth
from .config import Settings
from .downloader import Downloader, DownloadError, Interrupted
from .labapi import IndexingError, LabApi
from .netutil import UrlNotAllowed, check_url_syntax
from .ocfl import OcflError, OcflStorage, list_payload
from .store import Store
from .uploads import UploadConflict, UploadManager
from .util import InsufficientSpace, disk_status, ensure_space, human_bytes, now_iso

logger = logging.getLogger("data-ingester")

STAGES = ("fetch", "assemble", "extract", "prepare", "import", "index", "cleanup")
ACTIVE = ("queued", "running")
LOG_LIMIT = 300
EXPECTED_ERRORS = (
    assemble.AssembleError,
    crates.CrateError,
    DownloadError,
    UrlNotAllowed,
    OcflError,
    IndexingError,
    InsufficientSpace,
    UploadConflict,
)


class JobError(Exception):
    pass


class JobStateError(Exception):
    pass


class JobNotFound(Exception):
    pass


def _first_error(group: BaseExceptionGroup) -> BaseException:
    flat: list[BaseException] = []

    def walk(item: BaseException) -> None:
        if isinstance(item, BaseExceptionGroup):
            for inner in item.exceptions:
                walk(inner)
        else:
            flat.append(item)

    walk(group)
    for item in flat:
        if isinstance(item, Interrupted):
            return item
    return flat[0] if flat else RuntimeError("Unknown failure")


class JobManager:
    def __init__(self, settings: Settings, store: Store, auth: Auth):
        self.settings = settings
        self.store = store
        self.uploads = UploadManager(settings, store)
        self.staging = settings.work_dir / "ocfl-staging"
        self.ocfl = OcflStorage(settings.ocfl_root, self.staging)
        self.downloader = Downloader(settings)
        self.lab = LabApi(settings, auth)
        self.jobs: dict[str, dict] = {doc["id"]: doc for doc in store.all("jobs")}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.flags: dict[str, str] = {}
        self.current: str | None = None
        self.dirty: set[str] = set()
        self.tasks: list[asyncio.Task] = []

    def job_dir(self, job: dict) -> Path:
        return self.settings.work_dir / "jobs" / job["id"]

    async def start(self) -> None:
        self.settings.work_dir.mkdir(parents=True, exist_ok=True)
        if self.staging.exists():
            await asyncio.to_thread(shutil.rmtree, self.staging, True)
        self.staging.mkdir(parents=True, exist_ok=True)
        for job in sorted(self.jobs.values(), key=lambda item: item["createdAt"]):
            if job["status"] in ACTIVE or job["status"] == "deleting":
                if job["status"] == "deleting":
                    await self._purge(job)
                    continue
                job["status"] = "queued"
                self._log(job, "info", "Resuming automatically after the ingester restarted.")
                self._save(job, True)
                self.queue.put_nowait(job["id"])
        self.tasks = [
            asyncio.create_task(self._worker()),
            asyncio.create_task(self._flusher()),
            asyncio.create_task(self._janitor()),
        ]

    async def stop(self) -> None:
        if self.current:
            self.flags[self.current] = "shutdown"
            for _ in range(20):
                if not self.current:
                    break
                await asyncio.sleep(0.25)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self._flush()
        await self.downloader.close()
        await self.lab.close()

    def _log(self, job: dict, level: str, message: str) -> None:
        job.setdefault("log", []).append({"t": now_iso(), "level": level, "msg": message})
        if len(job["log"]) > LOG_LIMIT:
            del job["log"][: len(job["log"]) - LOG_LIMIT]
        method = {"warn": logger.warning, "error": logger.error}.get(level, logger.info)
        method("[%s] %s", job["id"][:8], message)

    def _save(self, job: dict, now: bool = False) -> None:
        job["updatedAt"] = now_iso()
        if now:
            self.store.put("jobs", job)
            self.dirty.discard(job["id"])
        else:
            self.dirty.add(job["id"])

    def _flush(self) -> None:
        ids = set(self.dirty)
        self.dirty.clear()
        if self.current:
            ids.add(self.current)
        for job_id in ids:
            job = self.jobs.get(job_id)
            if job:
                job["updatedAt"] = now_iso()
                self.store.put("jobs", job)

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                self._flush()
            except Exception:
                logger.exception("Persisting job state failed")

    async def _janitor(self) -> None:
        while True:
            try:
                removed = self.uploads.expire()
                if removed:
                    logger.info("Removed %s stale uploads", removed)
                jobs_root = self.settings.work_dir / "jobs"
                if jobs_root.exists():
                    for folder in jobs_root.iterdir():
                        if folder.is_dir() and folder.name not in self.jobs:
                            await asyncio.to_thread(shutil.rmtree, folder, True)
            except Exception:
                logger.exception("Janitor run failed")
            await asyncio.sleep(3600)

    def _cancelled(self, job: dict):
        job_id = job["id"]
        return lambda: self.flags.get(job_id)

    def _check(self, job: dict) -> None:
        reason = self.flags.get(job["id"])
        if reason:
            raise Interrupted(reason)

    def get(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if not job:
            raise JobNotFound(job_id)
        return job

    def listing(self, limit: int = 50) -> list[dict]:
        ordered = sorted(self.jobs.values(), key=lambda item: item["createdAt"], reverse=True)[:limit]
        return [self.view(job, 40) for job in ordered]

    def view(self, job: dict, log_tail: int | None = None) -> dict:
        data = {key: value for key, value in job.items() if key != "log"}
        entries = job.get("log", [])
        data["log"] = entries[-log_tail:] if log_tail else entries
        data["queuePosition"] = self._queue_position(job["id"])
        return data

    def _queue_position(self, job_id: str) -> int | None:
        waiting = [item for item in sorted(self.jobs.values(), key=lambda j: j["createdAt"]) if item["status"] == "queued"]
        for position, item in enumerate(waiting, start=1):
            if item["id"] == job_id:
                return position
        return None

    def _part(self, index: int, url: str | None = None, upload: dict | None = None) -> dict:
        return {
            "index": index,
            "url": url,
            "uploadId": upload["id"] if upload else None,
            "filename": upload["filename"] if upload else None,
            "size": upload["size"] if upload else None,
            "received": 0,
            "status": "pending",
            "speed": 0,
            "attempts": 0,
            "message": "",
            "ranges": None,
            "probed": False,
            "removed": False,
        }

    def create(self, request: dict, user: dict) -> dict:
        source = request.get("source")
        options = {"name": None, "payload": request.get("payload") or "all", "index": bool(request.get("index", True))}
        if options["payload"] not in ("all", "text"):
            raise ValueError("payload must be all or text.")
        if request.get("name"):
            options["name"] = crates.validate_name(request["name"])
        job_id = uuid.uuid4().hex
        parts: list[dict] = []
        if source == "url":
            urls = [item.strip() for item in request.get("urls") or [] if item and item.strip()]
            if not urls:
                raise ValueError("Add at least one download link.")
            if len(urls) > self.settings.max_urls:
                raise ValueError(f"At most {self.settings.max_urls} links can be ingested in one job.")
            if len(set(urls)) != len(urls):
                raise ValueError("The same link was added more than once.")
            for index, url in enumerate(urls):
                parts.append(self._part(index, url=check_url_syntax(url, self.settings)))
        elif source == "upload":
            ids = [item for item in request.get("uploadIds") or [] if item]
            if not ids:
                raise ValueError("Upload at least one file first.")
            if len(set(ids)) != len(ids):
                raise ValueError("The same upload was added more than once.")
            docs = [self.uploads.get(item) for item in ids]
            for doc in docs:
                if doc.get("jobId"):
                    raise UploadConflict(f"{doc['filename']} is already attached to another job.", doc["received"])
                if doc["status"] != "complete":
                    raise UploadConflict(f"{doc['filename']} is not fully uploaded yet.", doc["received"])
            for index, doc in enumerate(docs):
                parts.append(self._part(index, upload=doc))
            for doc in docs:
                self.uploads.attach(doc["id"], job_id)
        else:
            raise ValueError("source must be url or upload.")
        job = {
            "id": job_id,
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "createdBy": {"id": user.get("id"), "role": user.get("role")},
            "source": source,
            "options": options,
            "status": "queued",
            "stage": "fetch",
            "error": None,
            "parts": parts,
            "stages": {name: {"status": "pending", "done": 0, "total": None, "message": ""} for name in STAGES},
            "assembly": None,
            "extract": None,
            "crates": [],
            "log": [],
        }
        self.jobs[job_id] = job
        noun = "link" if source == "url" else "uploaded file"
        self._log(job, "info", f"Job created with {len(parts)} {noun}{'s' if len(parts) != 1 else ''}.")
        self._save(job, True)
        self.queue.put_nowait(job_id)
        return job

    def pause(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["status"] == "queued":
            job["status"] = "paused"
            self._log(job, "warn", "Paused before it started.")
            self._save(job, True)
            return job
        if job["status"] == "running":
            self.flags[job_id] = "pause"
            self._log(job, "info", "Pausing…")
            return job
        raise JobStateError("Only queued or running jobs can be paused.")

    def resume(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job_id == self.current:
            raise JobStateError("The job is still stopping; try again in a moment.")
        if job["status"] not in ("paused", "failed"):
            raise JobStateError("Only paused or failed jobs can be resumed.")
        for stage in job["stages"].values():
            if stage["status"] in ("failed", "paused", "running"):
                stage["status"] = "pending"
        job["status"] = "queued"
        job["error"] = None
        self._log(job, "info", "Resumed; continuing from the last completed step.")
        self._save(job, True)
        self.queue.put_nowait(job_id)
        return job

    def reindex(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["status"] not in ("completed", "failed") or job_id == self.current:
            raise JobStateError("Only completed or failed jobs can be re-indexed.")
        if not any(item.get("import") for item in job.get("crates", [])):
            raise JobStateError("Nothing has been imported yet, so there is nothing to index.")
        job["options"]["index"] = True
        job["stages"]["index"] = {"status": "pending", "done": 0, "total": None, "message": ""}
        for item in job["crates"]:
            item["index"] = None
        for name in ("fetch", "assemble", "extract", "prepare", "import"):
            if job["stages"][name]["status"] != "done":
                raise JobStateError("Earlier steps are incomplete; resume the job instead.")
        job["status"] = "queued"
        job["error"] = None
        self._log(job, "info", "Re-indexing requested.")
        self._save(job, True)
        self.queue.put_nowait(job_id)
        return job

    async def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if job_id == self.current:
            self.flags[job_id] = "delete"
            job["status"] = "deleting"
            self._save(job, True)
            return
        await self._purge(job)

    async def _purge(self, job: dict) -> None:
        await asyncio.to_thread(shutil.rmtree, self.job_dir(job), True)
        for part in job.get("parts", []):
            if part.get("uploadId"):
                self.uploads.release(part["uploadId"])
        self.jobs.pop(job["id"], None)
        self.dirty.discard(job["id"])
        self.store.delete("jobs", job["id"])

    async def status(self) -> dict:
        version = await self.lab.version()
        try:
            self.ocfl.load()
            ocfl = {"ready": True, "message": ""}
        except (OSError, ValueError, OcflError) as exc:
            ocfl = {"ready": False, "message": str(exc)}
        return {
            "disk": disk_status(self.settings, self.settings.work_dir),
            "current": self.current,
            "queued": sum(1 for job in self.jobs.values() if job["status"] == "queued"),
            "labApi": {"reachable": version is not None},
            "ocfl": ocfl,
            "limits": {
                "uploadChunkSize": self.settings.upload_chunk_size,
                "maxUploadBytes": self.settings.max_upload_bytes,
                "maxUrls": self.settings.max_urls,
            },
        }

    async def collections(self) -> list[dict]:
        def run() -> list[dict]:
            self.ocfl.load()
            return self.ocfl.list_objects()

        return await asyncio.to_thread(run)

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = self.jobs.get(job_id)
            if not job or job["status"] != "queued":
                continue
            self.current = job_id
            self.flags.pop(job_id, None)
            job["status"] = "running"
            job["error"] = None
            self._save(job, True)
            try:
                await self._run(job)
                job["status"] = "completed"
                job["stage"] = "done"
                self._log(job, "info", "Ingestion finished.")
            except Interrupted as exc:
                stage = job["stages"].get(job["stage"])
                if stage and stage["status"] == "running":
                    stage["status"] = "paused"
                if exc.reason == "pause":
                    job["status"] = "paused"
                    self._log(job, "warn", "Paused. Resume to continue from where it stopped.")
                elif exc.reason == "shutdown":
                    job["status"] = "queued"
                    self._log(job, "warn", "Interrupted by an ingester restart; it resumes automatically.")
                else:
                    job["status"] = "deleting"
            except asyncio.CancelledError:
                job["status"] = "queued"
                self._save(job, True)
                self.current = None
                raise
            except Exception as exc:
                job["status"] = "failed"
                job["error"] = str(exc) or exc.__class__.__name__
                stage = job["stages"].get(job["stage"])
                if stage:
                    stage["status"] = "failed"
                self._log(job, "error", job["error"])
                if not isinstance(exc, EXPECTED_ERRORS + (JobError, ValueError)):
                    logger.exception("Job %s failed", job_id)
            finally:
                self.current = None
                if self.flags.pop(job_id, None) == "delete" or job["status"] == "deleting":
                    await self._purge(job)
                elif job_id in self.jobs:
                    self._save(job, True)

    async def _run(self, job: dict) -> None:
        handlers = {
            "fetch": self._fetch,
            "assemble": self._assemble,
            "extract": self._extract,
            "prepare": self._prepare,
            "import": self._import,
            "index": self._index,
            "cleanup": self._cleanup,
        }
        for name in STAGES:
            stage = job["stages"][name]
            if stage["status"] in ("done", "skipped"):
                continue
            self._check(job)
            job["stage"] = name
            stage["status"] = "running"
            stage["message"] = ""
            stage["startedAt"] = now_iso()
            self._save(job, True)
            await handlers[name](job, stage)
            if stage["status"] == "running":
                stage["status"] = "done"
            stage["finishedAt"] = now_iso()
            self._save(job, True)

    def _part_path(self, job: dict, part: dict) -> Path:
        return self.job_dir(job) / "parts" / f"part-{part['index']:03d}.bin"

    async def _fetch(self, job: dict, stage: dict) -> None:
        parts_dir = self.job_dir(job) / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        if job["source"] == "upload":
            await self._take_uploads(job, stage)
            return
        cancelled = self._cancelled(job)
        for part in job["parts"]:
            if part["status"] == "done" or (part["size"] is not None and part.get("probed")):
                continue

            def on_retry(attempt: int, error: str, delay: float, part: dict = part) -> None:
                part["status"] = "retrying"
                part["attempts"] += 1
                part["message"] = f"{error} (retrying in {int(delay)} s)"
                self._log(job, "warn", f"Part {part['index'] + 1}: {error}; retrying in {int(delay)} s.")

            part["status"] = "checking"
            info = await self.downloader.probe(part["url"], on_retry, cancelled)
            part["size"] = info["size"]
            part["ranges"] = info["ranges"]
            part["probed"] = True
            part["filename"] = info["filename"] or part["filename"] or f"part-{part['index'] + 1:03d}"
            part["status"] = "pending"
            part["message"] = ""
            resumable = {
                True: "resumable",
                False: "not resumable (the source refuses byte ranges)",
                None: "resume support is checked on reconnect",
            }[info["ranges"]]
            self._log(job, "info", f"Part {part['index'] + 1}: {part['filename']}, {human_bytes(info['size'])}, {resumable}.")
        sizes = [part["size"] for part in job["parts"]]
        stage["total"] = sum(sizes) if all(size is not None for size in sizes) else None
        remaining = 0
        for part in job["parts"]:
            path = self._part_path(job, part)
            have = path.stat().st_size if path.exists() else 0
            part["received"] = have
            remaining += max(0, (part["size"] or 0) - have)
        stage["done"] = sum(part["received"] for part in job["parts"])
        ensure_space(self.settings, parts_dir, remaining, "download the files")
        pending = [part for part in job["parts"] if part["status"] != "done"]
        if not pending:
            return
        semaphore = asyncio.Semaphore(self.settings.download_concurrency)

        async def one(part: dict) -> None:
            async with semaphore:
                await self._download_part(job, stage, part)

        try:
            async with asyncio.TaskGroup() as group:
                for part in pending:
                    group.create_task(one(part))
        except BaseExceptionGroup as error:
            raise _first_error(error)

    async def _download_part(self, job: dict, stage: dict, part: dict) -> None:
        dest = self._part_path(job, part)
        part["status"] = "downloading"
        part["message"] = ""
        window = {"time": time.monotonic(), "bytes": dest.stat().st_size if dest.exists() else 0}

        def on_progress(received: int, size: int | None) -> None:
            if part["status"] == "retrying":
                part["status"] = "downloading"
                part["message"] = ""
            part["received"] = received
            if size is not None:
                part["size"] = size
            moment = time.monotonic()
            if moment - window["time"] >= 1:
                part["speed"] = int(max(0, received - window["bytes"]) / (moment - window["time"]))
                window["time"] = moment
                window["bytes"] = received
            stage["done"] = sum(item["received"] for item in job["parts"])

        def on_retry(attempt: int, error: str, delay: float) -> None:
            part["status"] = "retrying"
            part["attempts"] += 1
            part["speed"] = 0
            part["message"] = f"{error} (resuming in {int(delay)} s)"
            self._log(job, "warn", f"Part {part['index'] + 1}: {error}; resuming from {human_bytes(part['received'])} in {int(delay)} s.")

        if window["bytes"]:
            self._log(job, "info", f"Part {part['index'] + 1}: resuming from {human_bytes(window['bytes'])}.")
        received = await self.downloader.fetch(part["url"], dest, part["size"], on_progress, on_retry, self._cancelled(job))
        part["received"] = received
        part["size"] = part["size"] or received
        part["status"] = "done"
        part["speed"] = 0
        part["message"] = ""
        stage["done"] = sum(item["received"] for item in job["parts"])
        self._log(job, "info", f"Part {part['index'] + 1} downloaded ({human_bytes(received)}).")

    async def _take_uploads(self, job: dict, stage: dict) -> None:
        for part in job["parts"]:
            dest = self._part_path(job, part)
            if part["status"] == "done" and dest.exists():
                continue
            doc = self.uploads.items.get(part["uploadId"])
            if doc is None:
                if dest.exists() and dest.stat().st_size == part["size"]:
                    part["status"] = "done"
                    part["received"] = part["size"]
                    continue
                raise JobError(f"The uploaded file {part['filename']} is missing; upload it again.")
            source = self.uploads.path(doc)
            if not source.exists() or source.stat().st_size != doc["size"]:
                raise JobError(f"The uploaded file {part['filename']} is incomplete; upload it again.")
            await asyncio.to_thread(os.replace, source, dest)
            self.uploads.release(doc["id"])
            part["received"] = part["size"]
            part["status"] = "done"
        stage["total"] = sum(part["size"] or 0 for part in job["parts"])
        stage["done"] = stage["total"]
        self._log(job, "info", f"{len(job['parts'])} uploaded file(s) ready ({human_bytes(stage['total'])}).")

    async def _assemble(self, job: dict, stage: dict) -> None:
        paths = [self._part_path(job, part) for part in job["parts"]]
        names = [part.get("filename") for part in job["parts"]]
        for path, part in zip(paths, job["parts"]):
            if not path.exists():
                raise JobError(f"Part {part['index'] + 1} is missing on disk; resume the download.")
        plan = await asyncio.to_thread(assemble.plan, paths, names)
        relative = lambda path: path.relative_to(self.job_dir(job)).as_posix()
        if plan["kind"] == "spanned":
            ordered = [paths[index] for index in plan["order"]]
            ensure_space(self.settings, self.job_dir(job), sum(path.stat().st_size for path in ordered), "join the split zip")
            joined = await asyncio.to_thread(assemble.join_spanned, ordered, self.job_dir(job))
            groups = [[relative(joined)]]
            description = f"split zip of {len(paths)} parts joined into one archive"
        else:
            groups = [[relative(paths[index]) for index in group] for group in plan["groups"]]
            description = {
                "single": "a single zip archive",
                "multi": f"{len(paths)} separate zip archives merged into one crate folder",
                "concat": f"{len(paths)} split parts joined in order",
            }[plan["kind"]]
        job["assembly"] = {"kind": plan["kind"], "groups": groups}
        stage["message"] = description
        self._log(job, "info", f"Assembled {description}.")

    async def _extract(self, job: dict, stage: dict) -> None:
        job_dir = self.job_dir(job)
        groups = [[job_dir / name for name in group] for group in job["assembly"]["groups"]]
        dest = job_dir / "extract"
        if dest.exists():
            await asyncio.to_thread(shutil.rmtree, dest)
        members = await asyncio.to_thread(assemble.list_members, groups)
        if any(member["encrypted"] for member in members):
            raise JobError("The archive is password protected; upload an unencrypted zip.")
        roots = assemble.crate_roots(members)
        if not roots:
            raise JobError("No ro-crate-metadata.json was found in the archive.")
        allowed: set[str] | None = None
        if job["options"]["payload"] == "text":
            allowed = set()
            for root in roots:
                target = f"{root}/{crates.METADATA_NAME}" if root else crates.METADATA_NAME
                member = next(item for item in members if item["path"] == target)
                raw = await asyncio.to_thread(assemble.read_member, groups, member["group"], member["name"])
                try:
                    metadata = json.loads(raw.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise JobError(f"{target} is not valid JSON: {exc}") from exc
                allowed.add(target)
                for item in crates.text_payload(metadata):
                    allowed.add(f"{root}/{item}" if root else item)

        def keep(path: str) -> bool:
            return allowed is None or path in allowed

        planned = sum(member["size"] for member in members if member["path"] and not member["dir"] and keep(member["path"]))
        ensure_space(self.settings, job_dir, planned, "extract the archive")
        stage["total"] = planned
        stage["done"] = 0
        stats = await asyncio.to_thread(
            assemble.extract,
            groups,
            dest,
            keep,
            lambda done: stage.__setitem__("done", done),
            self._cancelled(job),
            Interrupted,
        )
        job["extract"] = {
            "files": stats["files"],
            "bytes": stats["bytes"],
            "skipped": stats["skipped"],
            "roots": roots,
        }
        summary = f"Extracted {stats['files']} files ({human_bytes(stats['bytes'])})"
        if stats["skipped"]:
            summary += f"; {stats['skipped']} non-text files left out (text-only payload)"
        self._log(job, "info", summary + ".")
        if stats["unsafe"]:
            self._log(job, "warn", f"Skipped {len(stats['unsafe'])} entries with unsafe paths, e.g. {stats['unsafe'][0]}.")
        if stats["symlinks"]:
            self._log(job, "warn", f"Skipped {stats['symlinks']} symbolic links.")
        if stats["duplicates"]:
            self._log(job, "warn", f"{stats['duplicates']} files appeared in more than one archive; the later copy was kept.")
        if len(roots) > 1:
            self._log(job, "info", f"Found {len(roots)} crates: {', '.join(root or '(archive root)' for root in roots)}.")

    def _remove_archives(self, job: dict) -> None:
        if self.settings.keep_archives:
            return
        job_dir = self.job_dir(job)
        removed = False
        for name in ("parts", "joined.zip"):
            target = job_dir / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                removed = True
            elif target.exists():
                target.unlink()
                removed = True
        for part in job["parts"]:
            part["removed"] = True
        if removed:
            self._log(job, "info", "Downloaded archives removed to free disk space.")

    def _prepare_one(self, crate_dir: Path, extract_dir: Path, custom: str | None, names: list[str], payload: str) -> dict:
        metadata = crates.load_metadata(crate_dir)
        graph = metadata["@graph"]
        descriptor = crates.find_descriptor(graph)
        about = crates.ref_ids(descriptor.get("about"))
        root_entity = next((item for item in graph if isinstance(item, dict) and about and item.get("@id") == about[0]), {})
        fallback = ""
        if not custom and not (about and about[0].startswith("arcp://")):
            fallback = crates.default_name(crate_dir, extract_dir, root_entity, names)
        report = crates.normalise(metadata, custom, fallback)
        crates.write_metadata(crate_dir, metadata)
        analysis = crates.analyse(metadata, report["rootId"], crate_dir)
        existing = self.ocfl.describe(report["rootId"])
        warnings = list(report["warnings"])
        missing = len(analysis["missing"])
        if missing and payload == "text":
            warnings.append(f"{missing} described files are not stored (text-only payload); their metadata is still indexed.")
        elif missing:
            warnings.append(f"{missing} files described in the metadata are not in the archive; their metadata is still indexed.")
        if analysis["counts"]["skipped"]:
            warnings.append(f"{analysis['counts']['skipped']} entities lack the LDaC profile the portal needs and will not be indexed.")
        names_value = crates.as_list(root_entity.get("name"))
        return {
            "rootId": report["rootId"],
            "previousRootId": report["previousRootId"],
            "title": str(names_value[0]) if names_value else report["rootId"],
            "dir": crate_dir.relative_to(extract_dir.parent).as_posix(),
            "changes": report["changes"],
            "warnings": warnings,
            "counts": analysis["counts"],
            "expectedMembers": analysis["expectedMembers"],
            "expectedByParent": analysis["expectedByParent"],
            "filesLocal": analysis["filesLocal"],
            "filesPresent": analysis["filesPresent"],
            "missingSample": analysis["missing"][:20],
            "missingCount": missing,
            "existing": existing,
            "import": None,
            "index": None,
        }

    async def _prepare(self, job: dict, stage: dict) -> None:
        await asyncio.to_thread(self._remove_archives, job)
        extract_dir = self.job_dir(job) / "extract"
        roots = job["extract"]["roots"]
        names = [part.get("filename") or "" for part in job["parts"]]
        custom = job["options"]["name"]
        if custom and len(roots) > 1:
            self._log(job, "warn", "A custom identifier applies to a single crate only; identifiers were derived for each crate.")
            custom = None
        prepared = []
        stage["total"] = len(roots)
        for index, root in enumerate(roots):
            self._check(job)
            crate_dir = extract_dir / root if root else extract_dir
            result = await asyncio.to_thread(self._prepare_one, crate_dir, extract_dir, custom, names, job["options"]["payload"])
            if any(item["rootId"] == result["rootId"] for item in prepared):
                raise JobError(f"Two crates in the archive use the same identifier {result['rootId']}.")
            prepared.append(result)
            stage["done"] = index + 1
            action = f"updates existing {result['existing']['head']}" if result["existing"] else "is new"
            self._log(
                job,
                "info",
                f"Prepared {result['rootId']} ({result['counts']['objects']} objects, {result['filesPresent']} of "
                f"{result['filesLocal']} files present); it {action}.",
            )
            for change in result["changes"]:
                self._log(job, "info", f"Normalised: {change}.")
            for warning in result["warnings"]:
                self._log(job, "warn", warning)
        job["crates"] = prepared

    async def _import(self, job: dict, stage: dict) -> None:
        job_dir = self.job_dir(job)
        sizes = []
        for item in job["crates"]:
            files = await asyncio.to_thread(list_payload, job_dir / item["dir"])
            sizes.append(sum(path.stat().st_size for _, path in files))
        stage["total"] = sum(sizes)
        stage["done"] = sum(size for size, item in zip(sizes, job["crates"]) if item.get("import"))
        if os.stat(job_dir).st_dev != os.stat(self.settings.ocfl_root).st_dev:
            ensure_space(self.settings, self.settings.ocfl_root, stage["total"], "copy the crate into the repository")
        for size, item in zip(sizes, job["crates"]):
            if item.get("import"):
                continue
            base = stage["done"]
            result = await asyncio.to_thread(
                self.ocfl.import_directory,
                item["rootId"],
                job_dir / item["dir"],
                f"Ingested by the RAPID-CDL data ingester (job {job['id'][:8]})",
                lambda done, total, base=base: stage.__setitem__("done", base + done),
                self._cancelled(job),
                Interrupted,
            )
            item["import"] = result
            stage["done"] = base + size
            if not result["changed"]:
                self._log(job, "info", f"{item['rootId']} is unchanged; OCFL head stays {result['version']}.")
            elif result["created"]:
                self._log(job, "info", f"{item['rootId']} imported into OCFL as {result['version']} ({result['files']} files).")
            else:
                self._log(
                    job,
                    "info",
                    f"{item['rootId']} updated to OCFL {result['version']} ({result['added']} new or changed files, "
                    f"{result['reused']} unchanged).",
                )

    async def _index(self, job: dict, stage: dict) -> None:
        if not job["options"]["index"]:
            stage["status"] = "skipped"
            self._log(job, "info", "Indexing skipped as requested; index it later from the admin page.")
            return
        targets = [item for item in job["crates"] if item.get("import")]
        stage["total"] = sum(item["expectedMembers"] for item in targets)
        for item in targets:
            if item.get("index") and item["index"].get("status") in ("indexed", "settled"):
                continue
            self._check(job)
            if "expectedByParent" not in item:
                stored = await asyncio.to_thread(self._stored_expectations, item["rootId"])
                if stored:
                    item["expectedMembers"] = stored["expectedMembers"]
                    item["expectedByParent"] = stored["expectedByParent"]
                    stage["total"] = sum(entry["expectedMembers"] for entry in targets)
            try:
                before = await self.lab.snapshot(item["rootId"])
            except httpx.HTTPError as exc:
                raise IndexingError(f"The portal API is unreachable: {exc}") from exc
            existed = bool(before["root"] or (before["members"] or 0) > 0)
            started = await self.lab.trigger(item["rootId"], force=True)
            self._log(job, "info", f"Indexing started for {item['rootId']} (HTTP {started['status']}).")
            item["index"] = {"status": "running", "members": before["members"] or 0, "expected": item["expectedMembers"], "root": before["root"]}

            def on_progress(snapshot: dict, item: dict = item) -> None:
                item["index"].update(snapshot)
                stage["done"] = sum((entry.get("index") or {}).get("members", 0) or 0 for entry in targets)

            result = await self.lab.wait(
                item["rootId"],
                item["expectedMembers"],
                existed,
                on_progress,
                self._cancelled(job),
                Interrupted,
                item.get("expectedByParent") or {},
            )
            item["index"].update(result)
            if result["status"] == "indexed":
                self._log(job, "info", f"{item['rootId']} indexed: {result['members']} members visible in the portal.")
            elif result["status"] == "settled":
                self._log(
                    job,
                    "warn",
                    f"{item['rootId']} indexing settled at {result['members']} of {result['expected']} expected members.",
                )
            else:
                self._log(
                    job,
                    "warn",
                    f"{item['rootId']} is still indexing after {self.settings.index_timeout // 60} minutes "
                    f"({result['members']} of {result['expected']} members); check the portal later or re-index.",
                )
        stage["message"] = (
            "Collection records are in the portal; full-text search indexing continues in the portal API for a few "
            "minutes after this step."
        )

    def _stored_expectations(self, root_id: str) -> dict | None:
        try:
            self.ocfl.load()
            metadata = json.loads(self.ocfl.read_file(root_id, crates.METADATA_NAME).decode("utf-8-sig"))
            analysis = crates.analyse(metadata, root_id, self.settings.work_dir / "missing")
        except (OSError, ValueError, KeyError, OcflError):
            return None
        return {"expectedMembers": analysis["expectedMembers"], "expectedByParent": analysis["expectedByParent"]}

    async def _cleanup(self, job: dict, stage: dict) -> None:
        await asyncio.to_thread(shutil.rmtree, self.job_dir(job), True)
        self._log(job, "info", "Working files removed.")
