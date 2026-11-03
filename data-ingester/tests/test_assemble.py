import io
import shutil
import subprocess
import zipfile

import pytest

from app import assemble
from conftest import build_zip


def _split(data: bytes, parts: int, folder, prefix="crate.zip."):
    size = len(data) // parts + 1
    paths = []
    for index in range(parts):
        path = folder / f"{prefix}{index + 1:03d}"
        path.write_bytes(data[index * size:(index + 1) * size])
        paths.append(path)
    return paths


def _extract_all(groups, dest):
    return assemble.extract(groups, dest, lambda path: True, lambda done: None, lambda: None, RuntimeError)


def test_single(tmp_path):
    path = tmp_path / "one.zip"
    path.write_bytes(build_zip())
    assert assemble.plan([path], ["one.zip"]) == {"kind": "single", "groups": [[0]]}
    stats = _extract_all([[path]], tmp_path / "out")
    assert stats["files"] == 5
    assert not (tmp_path / "out" / "__MACOSX").exists()
    assert not (tmp_path / "out" / "sample" / ".DS_Store").exists()


def test_raw_split_in_order_and_shuffled(tmp_path):
    paths = _split(build_zip(objects=3), 4, tmp_path)
    plan = assemble.plan(paths, [path.name for path in paths])
    assert plan == {"kind": "concat", "groups": [[0, 1, 2, 3]]}
    shuffled = [paths[2], paths[0], paths[3], paths[1]]
    plan = assemble.plan(shuffled, [path.name for path in shuffled])
    assert plan["kind"] == "concat" and plan["groups"] == [[1, 3, 0, 2]]
    group = [shuffled[index] for index in plan["groups"][0]]
    members = assemble.list_members([group])
    assert assemble.crate_roots(members) == [""]
    stats = _extract_all([group], tmp_path / "out")
    assert stats["files"] == 7


def test_split_missing_part_is_reported(tmp_path):
    paths = _split(build_zip(objects=3), 4, tmp_path)
    with pytest.raises(assemble.AssembleError):
        assemble.plan(paths[:3], [path.name for path in paths[:3]])


def test_multiple_standalone_archives(tmp_path):
    first = tmp_path / "a.zip"
    first.write_bytes(build_zip(objects=1))
    second = tmp_path / "b.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sample/extra/notes.txt", "extra")
    second.write_bytes(buffer.getvalue())
    assert assemble.plan([first, second], ["a.zip", "b.zip"])["kind"] == "multi"
    stats = _extract_all([[first], [second]], tmp_path / "out")
    assert (tmp_path / "out" / "sample" / "extra" / "notes.txt").read_text() == "extra"
    assert stats["files"] == 4


@pytest.mark.skipif(shutil.which("zip") is None, reason="zip tool not installed")
def test_spanned_zip(tmp_path):
    source = tmp_path / "src"
    (source / "sample").mkdir(parents=True)
    (source / "ro-crate-metadata.json").write_text("{}")
    (source / "sample" / "big.bin").write_bytes(bytes(range(256)) * 4000)
    subprocess.run(["zip", "-q", "-0", "-r", "-s", "400k", str(tmp_path / "set.zip"), "."], cwd=source, check=True)
    parts = sorted(tmp_path.glob("set.z*"))
    names = [path.name for path in parts]
    assert names[-1] == "set.zip" and len(parts) >= 3
    reordered = [parts[-1]] + parts[:-1]
    plan = assemble.plan(reordered, [path.name for path in reordered])
    assert plan["kind"] == "spanned"
    ordered = [reordered[index] for index in plan["order"]]
    joined = assemble.join_spanned(ordered, tmp_path / "work")
    stats = _extract_all([[joined]], tmp_path / "out")
    assert (tmp_path / "out" / "sample" / "big.bin").read_bytes() == bytes(range(256)) * 4000
    assert stats["files"] == 2
    with pytest.raises(assemble.AssembleError):
        assemble.plan([parts[-1]], ["set.zip"])


def test_unsafe_members_are_skipped(tmp_path):
    path = tmp_path / "evil.zip"
    path.write_bytes(build_zip(objects=1, extra={"../escape.txt": b"x", "/abs.txt": b"y"}))
    stats = _extract_all([[path]], tmp_path / "out")
    assert len(stats["unsafe"]) == 2
    assert not (tmp_path / "escape.txt").exists()


def test_nested_crate_roots():
    members = [
        {"path": "a/ro-crate-metadata.json", "dir": False},
        {"path": "a/inner/ro-crate-metadata.json", "dir": False},
        {"path": "b/ro-crate-metadata.json", "dir": False},
    ]
    assert assemble.crate_roots(members) == ["a", "b"]


def test_not_a_zip(tmp_path):
    path = tmp_path / "page.html"
    path.write_text("<html>expired</html>")
    with pytest.raises(assemble.AssembleError):
        assemble.plan([path], ["page.html"])
