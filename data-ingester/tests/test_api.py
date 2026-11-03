import json
import time
from dataclasses import replace

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import build_zip
from servers import FileServer, LabApiStub


def _token(settings, role="INDEX_ADMIN"):
    return jwt.encode({"id": 1, "email": "x", "role": role, "exp": int(time.time()) + 600}, settings.jwt_secret, algorithm="HS256")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        test_client.headers["Authorization"] = f"Bearer {_token(settings)}"
        yield test_client


def test_health_and_auth(settings):
    with TestClient(create_app(settings)) as anonymous:
        assert anonymous.get("/health").json() == {"status": "ok"}
        assert anonymous.get("/jobs").status_code == 401
        assert anonymous.get("/jobs", headers={"Authorization": "Bearer nope"}).status_code == 401
        wrong = jwt.encode({"role": "VIEWER"}, settings.jwt_secret, algorithm="HS256")
        assert anonymous.get("/jobs", headers={"Authorization": f"Bearer {wrong}"}).status_code == 403


def test_resumable_upload_protocol(client):
    data = build_zip(objects=1)
    created = client.post("/uploads", json={"filename": "crate.zip", "size": len(data), "fingerprint": "fp-1"}).json()
    upload_id = created["id"]
    first = client.patch(f"/uploads/{upload_id}", content=data[:1000], headers={"Upload-Offset": "0"})
    assert first.status_code == 200 and first.json()["received"] == 1000
    conflict = client.patch(f"/uploads/{upload_id}", content=data[1000:2000], headers={"Upload-Offset": "0"})
    assert conflict.status_code == 409 and conflict.json()["offset"] == 1000
    again = client.post("/uploads", json={"filename": "crate.zip", "size": len(data), "fingerprint": "fp-1"}).json()
    assert again["id"] == upload_id and again["received"] == 1000
    rest = client.patch(f"/uploads/{upload_id}", content=data[1000:], headers={"Upload-Offset": "1000"})
    assert rest.json()["status"] == "complete"
    too_much = client.patch(f"/uploads/{upload_id}", content=b"x", headers={"Upload-Offset": str(len(data))})
    assert too_much.status_code == 413
    assert client.get(f"/uploads/{upload_id}").json()["received"] == len(data)


def test_rejects_bad_requests(client):
    assert client.post("/jobs", json={"source": "url", "urls": []}).status_code == 400
    assert client.post("/jobs", json={"source": "url", "urls": ["ftp://x/y.zip"]}).status_code == 400
    assert client.post("/jobs", json={"source": "url", "urls": ["https://x.org/a.zip"], "name": "bad name"}).status_code == 400
    assert client.post("/jobs", json={"source": "upload", "uploadIds": ["missing"]}).status_code == 404


def _wait(client, job_id, until=("completed", "failed"), timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in until:
            return job
        time.sleep(0.2)
    raise AssertionError(json.dumps(job, indent=1)[:4000])


def test_full_pipeline_from_split_links(settings):
    data = build_zip(objects=3)
    size = len(data) // 3 + 1
    files = {f"crate.zip.{index + 1:03d}": data[index * size:(index + 1) * size] for index in range(3)}
    with FileServer(files, drop_after=50_000) as source, LabApiStub(settings.jwt_secret) as lab:
        lab.indexed["arcp://name,sample"] = 3
        configured = replace(settings, lab_api_url=lab.base)
        with TestClient(create_app(configured)) as test_client:
            test_client.headers["Authorization"] = f"Bearer {_token(configured)}"
            urls = [f"{source.base}/files/{name}" for name in files]
            created = test_client.post("/jobs", json={"source": "url", "urls": urls, "payload": "all"})
            assert created.status_code == 201, created.text
            job = _wait(test_client, created.json()["id"])
            assert job["status"] == "completed", json.dumps(job, indent=1)[:4000]
            assert job["assembly"]["kind"] == "concat"
            assert job["crates"][0]["rootId"] == "arcp://name,sample"
            assert job["crates"][0]["import"]["version"] == "v1"
            assert job["crates"][0]["index"]["status"] == "indexed"
            assert lab.posts == ["arcp://name,sample(?:/|$)"]
            assert any(part["attempts"] for part in job["parts"]) or source.drops == 1
            collections = test_client.get("/collections").json()["collections"]
            assert collections[0]["id"] == "arcp://name,sample"
            text_job = test_client.post(
                "/jobs", json={"source": "url", "urls": urls, "payload": "text", "name": "sample"}
            ).json()
            job = _wait(test_client, text_job["id"])
            assert job["status"] == "completed", json.dumps(job, indent=1)[:4000]
            assert job["crates"][0]["import"]["version"] == "v2"
            assert job["crates"][0]["existing"]["head"] == "v1"
            listing = test_client.get("/collections").json()["collections"]
            assert listing[0]["head"] == "v2" and listing[0]["files"] == 4
            assert not (configured.work_dir / "jobs" / job["id"]).exists()


def test_pipeline_from_uploads(settings):
    data = build_zip(objects=2)
    with LabApiStub(settings.jwt_secret) as lab:
        configured = replace(settings, lab_api_url=lab.base)
        lab.indexed["arcp://name,custom-name"] = 2
        with TestClient(create_app(configured)) as test_client:
            test_client.headers["Authorization"] = f"Bearer {_token(configured, 'ROLES_ADMIN')}"
            upload = test_client.post("/uploads", json={"filename": "crate.zip", "size": len(data), "fingerprint": "u1"}).json()
            for offset in range(0, len(data), 100_000):
                response = test_client.patch(
                    f"/uploads/{upload['id']}", content=data[offset:offset + 100_000], headers={"Upload-Offset": str(offset)}
                )
                assert response.status_code == 200
            created = test_client.post(
                "/jobs", json={"source": "upload", "uploadIds": [upload["id"]], "name": "custom-name"}
            ).json()
            job = _wait(test_client, created["id"])
            assert job["status"] == "completed", json.dumps(job, indent=1)[:4000]
            assert job["crates"][0]["rootId"] == "arcp://name,custom-name"
            assert test_client.get("/uploads").json()["uploads"] == []


def test_failed_link_can_be_removed(settings):
    with FileServer({}) as source:
        with TestClient(create_app(settings)) as test_client:
            test_client.headers["Authorization"] = f"Bearer {_token(settings)}"
            created = test_client.post("/jobs", json={"source": "url", "urls": [source.base + "/files/gone.zip"]}).json()
            job = _wait(test_client, created["id"])
            assert job["status"] == "failed" and "HTTP 404" in job["error"]
            assert test_client.post(f"/jobs/{job['id']}/reindex").status_code == 409
            assert test_client.delete(f"/jobs/{job['id']}").status_code == 204
            assert test_client.get(f"/jobs/{job['id']}").status_code == 404
