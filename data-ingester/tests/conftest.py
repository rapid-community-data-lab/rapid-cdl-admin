import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings


def docsearch_metadata(objects: int = 2) -> dict:
    graph = [
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.2"},
            "about": {"@id": "./"},
        },
        {
            "@id": "./",
            "@type": ["Dataset", "RepositoryCollection"],
            "name": "Sample Commission — Document Collection",
            "license": {"@id": "https://creativecommons.org/licenses/by/4.0/"},
            "hasPart": [{"@id": f"#inquiry-{index}"} for index in range(objects)],
        },
    ]
    for index in range(objects):
        graph.append(
            {
                "@id": f"#inquiry-{index}",
                "@type": ["Dataset", "RepositoryObject"],
                "name": f"Inquiry {index}",
                "isPartOf": {"@id": "./"},
                "hasPart": [{"@id": f"sample/R{index:03d}/doc.pdf"}, {"@id": f"sample/R{index:03d}/doc.pdf.txt"}],
            }
        )
        graph.append(
            {
                "@id": f"sample/R{index:03d}/doc.pdf",
                "@type": "File",
                "encodingFormat": "application/pdf",
                "isPartOf": {"@id": f"#inquiry-{index}"},
                "ds:extractedText": {"@id": f"sample/R{index:03d}/doc.pdf.txt"},
            }
        )
        graph.append(
            {
                "@id": f"sample/R{index:03d}/doc.pdf.txt",
                "@type": "File",
                "encodingFormat": "text/plain",
                "isPartOf": {"@id": f"#inquiry-{index}"},
            }
        )
    return {"@context": ["https://w3id.org/ro/crate/1.2/context"], "@graph": graph}


def build_zip(objects: int = 2, payload_size: int = 200_000, extra: dict | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ro-crate-metadata.json", json.dumps(docsearch_metadata(objects)))
        for index in range(objects):
            archive.writestr(f"sample/R{index:03d}/doc.pdf", os.urandom(payload_size))
            archive.writestr(f"sample/R{index:03d}/doc.pdf.txt", f"text of document {index}\n" * 50)
        archive.writestr("__MACOSX/sample/._doc.pdf", b"junk")
        archive.writestr("sample/.DS_Store", b"junk")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def make_ocfl_root(root: Path) -> Path:
    data = root / "data"
    (data / "extensions" / "000N-path-direct-storage-layout").mkdir(parents=True)
    (data / "0=ocfl_1.1").write_text("ocfl_1.1\n")
    (data / "ocfl_layout.json").write_text(
        json.dumps({"extension": "000N-path-direct-storage-layout", "description": "OCFL object identifiers are used directly as the path."})
    )
    (data / "extensions" / "000N-path-direct-storage-layout" / "config.json").write_text(
        json.dumps({"extensionName": "000N-path-direct-storage-layout"})
    )
    return data


@pytest.fixture
def settings(tmp_path, monkeypatch):
    ocfl = make_ocfl_root(tmp_path / "ocfl")
    monkeypatch.setenv("API_AUTH_JWT_SECRET", "test-secret-value-that-is-long-enough-123456")
    monkeypatch.setenv("OCFL_PATH", str(ocfl))
    monkeypatch.setenv("INGEST_WORK_DIR", str(tmp_path / "ocfl" / "work"))
    monkeypatch.setenv("ALLOW_PRIVATE_URLS", "true")
    monkeypatch.setenv("ALLOW_HTTP", "true")
    monkeypatch.setenv("MIN_FREE_BYTES", "1024")
    monkeypatch.setenv("MIN_FREE_PERCENT", "0")
    monkeypatch.setenv("DOWNLOAD_RETRIES", "5")
    monkeypatch.setenv("RETRY_BACKOFF_MAX", "0.2")
    monkeypatch.setenv("INDEX_POLL_INTERVAL", "0.2")
    monkeypatch.setenv("INDEX_SETTLE_SECONDS", "1")
    monkeypatch.setenv("INDEX_TIMEOUT", "20")
    return load_settings()
