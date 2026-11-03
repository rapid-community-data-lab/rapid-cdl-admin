import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from .auth import Auth
from .config import Settings, load_settings
from .crate import CrateError
from .jobs import JobManager, JobNotFound, JobStateError
from .netutil import UrlNotAllowed
from .ocfl import OcflError
from .store import Store
from .uploads import UploadConflict, UploadNotFound, UploadTooLarge
from .util import InsufficientSpace

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class JobRequest(BaseModel):
    source: Literal["url", "upload"]
    urls: list[str] = Field(default_factory=list, max_length=500)
    uploadIds: list[str] = Field(default_factory=list, max_length=500)
    name: str | None = Field(default=None, max_length=160)
    payload: Literal["all", "text"] = "all"
    index: bool = True


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    fingerprint: str = Field(min_length=1, max_length=512)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or load_settings()
    auth = Auth(config)
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(config.state_dir / "ingester.db")
        manager = JobManager(config, store, auth)
        await manager.start()
        state["manager"] = manager
        try:
            yield
        finally:
            await manager.stop()
            store.close()

    app = FastAPI(title="RAPID-CDL data ingester", version="1.0.0", lifespan=lifespan)
    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Upload-Offset"],
            expose_headers=["Upload-Offset"],
        )

    def manager() -> JobManager:
        return state["manager"]

    def user(authorization: str | None = Header(default=None)) -> dict:
        return auth.verify(authorization)

    @app.exception_handler(JobNotFound)
    async def job_not_found(_: Request, exc: JobNotFound):
        return JSONResponse({"detail": "Job not found."}, status_code=404)

    @app.exception_handler(UploadNotFound)
    async def upload_not_found(_: Request, exc: UploadNotFound):
        return JSONResponse({"detail": "Upload not found."}, status_code=404)

    @app.exception_handler(UploadConflict)
    async def upload_conflict(_: Request, exc: UploadConflict):
        return JSONResponse({"detail": str(exc), "offset": exc.offset}, status_code=409, headers={"Upload-Offset": str(exc.offset)})

    @app.exception_handler(UploadTooLarge)
    async def upload_too_large(_: Request, exc: UploadTooLarge):
        return JSONResponse({"detail": str(exc)}, status_code=413)

    @app.exception_handler(JobStateError)
    async def job_state(_: Request, exc: JobStateError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(InsufficientSpace)
    async def no_space(_: Request, exc: InsufficientSpace):
        return JSONResponse({"detail": str(exc)}, status_code=507)

    @app.exception_handler(CrateError)
    async def crate_error(_: Request, exc: CrateError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(UrlNotAllowed)
    async def url_not_allowed(_: Request, exc: UrlNotAllowed):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/status")
    async def service_status(_: dict = Depends(user)):
        return await manager().status()

    @app.get("/collections")
    async def collections(_: dict = Depends(user)):
        try:
            return {"collections": await manager().collections()}
        except (OSError, OcflError) as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"The OCFL repository is not available: {exc}")

    @app.get("/jobs")
    async def list_jobs(limit: int = 50, _: dict = Depends(user)):
        return {"jobs": manager().listing(max(1, min(limit, 200)))}

    @app.post("/jobs", status_code=201)
    async def create_job(body: JobRequest, current: dict = Depends(user)):
        job = manager().create(body.model_dump(), current)
        return manager().view(job)

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str, _: dict = Depends(user)):
        return manager().view(manager().get(job_id))

    @app.post("/jobs/{job_id}/pause")
    async def pause_job(job_id: str, _: dict = Depends(user)):
        return manager().view(manager().pause(job_id), 40)

    @app.post("/jobs/{job_id}/resume")
    async def resume_job(job_id: str, _: dict = Depends(user)):
        return manager().view(manager().resume(job_id), 40)

    @app.post("/jobs/{job_id}/reindex")
    async def reindex_job(job_id: str, _: dict = Depends(user)):
        return manager().view(manager().reindex(job_id), 40)

    @app.delete("/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str, _: dict = Depends(user)):
        await manager().delete(job_id)

    @app.get("/uploads")
    async def list_uploads(_: dict = Depends(user)):
        return {"uploads": manager().uploads.list()}

    @app.post("/uploads", status_code=201)
    async def create_upload(body: UploadRequest, current: dict = Depends(user)):
        doc = manager().uploads.create(body.filename, body.size, body.fingerprint, current)
        return manager().uploads.public(doc)

    @app.get("/uploads/{upload_id}")
    async def get_upload(upload_id: str, _: dict = Depends(user)):
        doc = manager().uploads.get(upload_id)
        return JSONResponse(manager().uploads.public(doc), headers={"Upload-Offset": str(doc["received"])})

    @app.patch("/uploads/{upload_id}")
    async def write_upload(
        upload_id: str,
        request: Request,
        upload_offset: int = Header(alias="Upload-Offset", ge=0),
        _: dict = Depends(user),
    ):
        try:
            doc = await manager().uploads.write(upload_id, upload_offset, request.stream())
        except ClientDisconnect:
            doc = manager().uploads.get(upload_id)
            return JSONResponse(manager().uploads.public(doc), status_code=400, headers={"Upload-Offset": str(doc["received"])})
        return JSONResponse(manager().uploads.public(doc), headers={"Upload-Offset": str(doc["received"])})

    @app.delete("/uploads/{upload_id}", status_code=204)
    async def delete_upload(upload_id: str, _: dict = Depends(user)):
        manager().uploads.delete(upload_id)

    return app


app = create_app()
