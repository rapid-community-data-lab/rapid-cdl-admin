import hashlib
import json

import pytest

from app.ocfl import OcflError, OcflStorage, map_identifier

CONFIG = {"suffix": "/__object__", "omitScheme": False, "replace": []}


@pytest.mark.parametrize(
    "identifier, expected",
    [
        ("arcp://name,alrc", "arcp_name_alrc/__object__"),
        ("arcp://name,bundled", "arcp_name_bundled/__object__"),
        ("arcp://name,doi10.4225%2F35%2F555d661071c76", "arcp_name_doi10.4225%2F35%2F555d661071c76/__object__"),
        ("arcp://name,hdl10.25949~24769173.v1", "arcp_name_hdl10.25949~24769173.v1/__object__"),
        ("arcp://name,pc-inquiry-submissions/circular-economy", "arcp_name_pc-inquiry-submissions/circular-economy/__object__"),
    ],
)
def test_map_identifier_matches_ocfl_fs(identifier, expected):
    assert map_identifier(identifier, CONFIG) == expected


def _crate(folder, files):
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return folder


def _verify(object_root):
    text = (object_root / "inventory.json").read_text()
    sidecar = (object_root / "inventory.json.sha512").read_text().split()
    assert sidecar[0] == hashlib.sha512(text.encode()).hexdigest()
    assert sidecar[1] == "inventory.json"
    return json.loads(text)


def test_import_create_update_unchanged(settings, tmp_path):
    storage = OcflStorage(settings.ocfl_root, tmp_path / "ocfl" / "work" / "staging")
    files = {"ro-crate-metadata.json": b"{}", "a/one.txt": b"one", "a/two.txt": b"two", "dup.txt": b"one"}
    src = _crate(tmp_path / "ocfl" / "work" / "crate", files)
    noop = lambda *args: None
    result = storage.import_directory("arcp://name,test", src, "first", noop, lambda: None, RuntimeError)
    assert result == {"version": "v1", "created": True, "changed": True, "added": 3, "reused": 0, "files": 4, "bytes": 11}
    root = settings.ocfl_root / "arcp_name_test" / "__object__"
    assert (root / "0=ocfl_object_1.1").read_text() == "ocfl_object_1.1\n"
    inventory = _verify(root)
    assert _verify(root / "v1") == inventory
    assert inventory["head"] == "v1"
    assert len(inventory["fixity"]["crc32"]) == 3
    one = hashlib.sha512(b"one").hexdigest()
    assert sorted(inventory["versions"]["v1"]["state"][one]) == ["a/one.txt", "dup.txt"]
    (src / "a" / "two.txt").write_bytes(b"two-changed")
    (src / "dup.txt").unlink()
    result = storage.import_directory("arcp://name,test", src, "second", noop, lambda: None, RuntimeError)
    assert result["version"] == "v2" and result["added"] == 1 and result["reused"] == 2
    inventory = _verify(root)
    state = inventory["versions"]["v2"]["state"]
    assert state[one] == ["a/one.txt"]
    assert inventory["manifest"][hashlib.sha512(b"two-changed").hexdigest()] == ["v2/content/a/two.txt"]
    assert (root / "v2" / "content" / "a" / "two.txt").read_bytes() == b"two-changed"
    assert not (root / "v2" / "content" / "a" / "one.txt").exists()
    result = storage.import_directory("arcp://name,test", src, "third", noop, lambda: None, RuntimeError)
    assert result["changed"] is False and result["version"] == "v2"
    assert storage.describe("arcp://name,test")["files"] == 3
    assert [item["id"] for item in storage.list_objects()] == ["arcp://name,test"]


def test_import_refuses_foreign_directory(settings, tmp_path):
    storage = OcflStorage(settings.ocfl_root, tmp_path / "ocfl" / "work" / "staging")
    src = _crate(tmp_path / "ocfl" / "work" / "crate", {"ro-crate-metadata.json": b"{}"})
    (settings.ocfl_root / "arcp_name_busy" / "__object__").mkdir(parents=True)
    (settings.ocfl_root / "arcp_name_busy" / "__object__" / "stray").write_text("x")
    with pytest.raises(OcflError):
        storage.import_directory("arcp://name,busy", src, "x", lambda *a: None, lambda: None, RuntimeError)


def test_import_recovers_orphan_version(settings, tmp_path):
    storage = OcflStorage(settings.ocfl_root, tmp_path / "ocfl" / "work" / "staging")
    src = _crate(tmp_path / "ocfl" / "work" / "crate", {"ro-crate-metadata.json": b"{}"})
    storage.import_directory("arcp://name,orphan", src, "x", lambda *a: None, lambda: None, RuntimeError)
    root = settings.ocfl_root / "arcp_name_orphan" / "__object__"
    (root / "v2" / "content").mkdir(parents=True)
    (src / "new.txt").write_text("new")
    result = storage.import_directory("arcp://name,orphan", src, "x", lambda *a: None, lambda: None, RuntimeError)
    assert result["version"] == "v2"
    assert (root / "v2" / "content" / "new.txt").exists()
