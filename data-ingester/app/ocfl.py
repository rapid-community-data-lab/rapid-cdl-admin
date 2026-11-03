import errno
import hashlib
import json
import os
import posixpath
import re
import shutil
import uuid
import zlib
from collections.abc import Callable
from pathlib import Path

from .util import now_iso

STORAGE_NAMASTE = "0=ocfl_1.1"
OBJECT_NAMASTE = "0=ocfl_object_1.1"
INVENTORY = "inventory.json"
INVENTORY_TYPE = "https://ocfl.io/1.1/spec/#inventory"
LAYOUT_FILE = "ocfl_layout.json"
PATH_DIRECT = "000N-path-direct-storage-layout"
SPECIAL_SCHEMES = {"http", "https", "ws", "wss", "ftp", "file"}
HASH_CHUNK = 1024 * 1024
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


class OcflError(Exception):
    pass


def _percent_encode(text: str, extra: str) -> str:
    out = []
    for char in text:
        code = ord(char)
        if code < 0x21 or code > 0x7E or char in extra:
            out.append("".join(f"%{byte:02X}" for byte in char.encode("utf-8")))
        else:
            out.append(char)
    return "".join(out)


def _node_join(*parts: str) -> str:
    joined = "/".join(part for part in parts if part != "")
    if not joined:
        return "."
    normal = posixpath.normpath(joined)
    if joined.endswith("/") and normal != "/":
        normal += "/"
    return normal


def map_identifier(identifier: str, config: dict) -> str:
    suffix = config.get("suffix", "/__object__")
    if suffix and identifier.endswith(suffix):
        raise OcflError(f"Identifier cannot end with {suffix}")
    value = identifier or ""
    for pattern, replacement in config.get("replace", []) or []:
        value = value.replace(pattern, replacement, 1)
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):(.*)$", value, re.S)
    if match and not re.search(r"[\s]", match.group(1)):
        scheme, rest = match.group(1).lower(), match.group(2)
        hostname, path, query, fragment = "", rest, "", ""
        if "#" in path:
            path, fragment = path.split("#", 1)
            fragment = "#" + _percent_encode(fragment, '"<>`')
        if "?" in path:
            path, query = path.split("?", 1)
            query = "?" + _percent_encode(query, "\"#<>'" if scheme in SPECIAL_SCHEMES else "\"#<>")
        if path.startswith("//"):
            position = path.find("/", 2)
            authority_end = position if position >= 0 else len(path)
            authority = path[2:authority_end]
            path = path[authority_end:]
            if "@" in authority:
                authority = authority.rsplit("@", 1)[1]
            port = re.search(r":(\d*)$", authority)
            if port:
                authority = authority[: port.start()]
            hostname = authority.lower() if scheme in SPECIAL_SCHEMES else _percent_encode(authority, "")
        pathname = _percent_encode(path, '"#<>?`{}')
        if pathname and not pathname.startswith("/") and hostname:
            pathname = "/" + pathname
        parts = []
        if not config.get("omitScheme", False) and scheme != "file":
            parts.append(scheme)
        if hostname:
            parts.append(hostname.replace(",", "_", 1).replace(";", "/", 1))
        value = _node_join("_".join(parts), pathname + query + fragment)
    value = re.sub(r"^/+", "", value)
    value = re.sub(r"/+$", "", value)
    mapped = _node_join(value, suffix or "")
    if mapped.startswith("../") or mapped == ".." or mapped.startswith("/"):
        raise OcflError(f"Identifier {identifier} maps outside the storage root")
    return mapped


def digest_file(path: Path, algorithm: str, on_bytes: Callable[[int], None]) -> tuple[str, str, int]:
    hasher = hashlib.new(algorithm)
    crc = 0
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK)
            if not chunk:
                break
            hasher.update(chunk)
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
            on_bytes(len(chunk))
    return hasher.hexdigest(), f"{crc & 0xFFFFFFFF:08x}", size


def _write_json(path: Path, data: dict) -> str:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    temp = path.with_name(path.name + f".tmp-{uuid.uuid4().hex[:8]}")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)
    return text


def _write_inventory(folder: Path, inventory: dict) -> None:
    text = _write_json(folder / INVENTORY, inventory)
    algorithm = inventory["digestAlgorithm"]
    digest = hashlib.new(algorithm, text.encode("utf-8")).hexdigest()
    sidecar = folder / f"{INVENTORY}.{algorithm}"
    temp = sidecar.with_name(sidecar.name + f".tmp-{uuid.uuid4().hex[:8]}")
    temp.write_text(f"{digest} {INVENTORY}", encoding="utf-8")
    os.replace(temp, sidecar)


def _place(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)
    os.chmod(target, 0o644)


def _move_dir(source: Path, target: Path) -> None:
    try:
        os.replace(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copytree(source, target)
        shutil.rmtree(source, ignore_errors=True)


def _version_name(head: str | None, number: int) -> str:
    if head and re.match(r"^v0\d+$", head):
        return f"v{number:0{len(head) - 1}d}"
    return f"v{number}"


def list_payload(src_dir: Path) -> list[tuple[str, Path]]:
    files = []
    for path in sorted(src_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.name in JUNK_NAMES or path.name.startswith("._"):
            continue
        files.append((path.relative_to(src_dir).as_posix(), path))
    return files


class OcflStorage:
    def __init__(self, root: Path, scratch: Path):
        self.root = root
        self.scratch = scratch
        self.config: dict | None = None

    def load(self) -> None:
        if not (self.root / STORAGE_NAMASTE).is_file():
            raise OcflError(f"{self.root} is not an OCFL 1.1 storage root ({STORAGE_NAMASTE} missing).")
        layout = json.loads((self.root / LAYOUT_FILE).read_text(encoding="utf-8"))
        name = layout.get("extension")
        if name != PATH_DIRECT:
            raise OcflError(f"Unsupported OCFL storage layout {name}; expected {PATH_DIRECT}.")
        config_path = self.root / "extensions" / name / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
        self.config = {"suffix": "/__object__", "omitScheme": False, "replace": [], **config}

    def object_root(self, identifier: str) -> Path:
        if self.config is None:
            self.load()
        return self.root / map_identifier(identifier, self.config)

    def read_inventory(self, object_root: Path) -> dict:
        text = (object_root / INVENTORY).read_text(encoding="utf-8")
        inventory = json.loads(text)
        algorithm = inventory.get("digestAlgorithm", "sha512")
        sidecar = (object_root / f"{INVENTORY}.{algorithm}").read_text(encoding="utf-8").split()
        actual = hashlib.new(algorithm, text.encode("utf-8")).hexdigest()
        if not sidecar or sidecar[0] != actual:
            raise OcflError(f"Inventory digest mismatch in {object_root}")
        return inventory

    def describe(self, identifier: str) -> dict | None:
        object_root = self.object_root(identifier)
        if not (object_root / OBJECT_NAMASTE).is_file():
            return None
        inventory = self.read_inventory(object_root)
        head = inventory["head"]
        state = inventory["versions"][head]["state"]
        return {
            "id": inventory["id"],
            "head": head,
            "versions": len(inventory["versions"]),
            "files": sum(len(paths) for paths in state.values()),
            "updated": inventory["versions"][head].get("created"),
        }

    def read_file(self, identifier: str, logical_path: str) -> bytes:
        object_root = self.object_root(identifier)
        inventory = self.read_inventory(object_root)
        state = inventory["versions"][inventory["head"]]["state"]
        digest = next((key for key, paths in state.items() if logical_path in paths), None)
        if digest is None:
            raise OcflError(f"{logical_path} is not in {identifier}")
        return (object_root / inventory["manifest"][digest][0]).read_bytes()

    def list_objects(self, limit: int = 500) -> list[dict]:
        found = []
        for current, folders, files in os.walk(self.root):
            if OBJECT_NAMASTE in files:
                folders[:] = []
                try:
                    inventory = self.read_inventory(Path(current))
                    head = inventory["head"]
                    version = inventory["versions"][head]
                    found.append(
                        {
                            "id": inventory["id"],
                            "head": head,
                            "files": sum(len(paths) for paths in version["state"].values()),
                            "updated": version.get("created"),
                        }
                    )
                except (OSError, ValueError, KeyError, OcflError):
                    continue
                if len(found) >= limit:
                    break
            else:
                folders[:] = [name for name in folders if name != "extensions" and not name.startswith(".")]
        return sorted(found, key=lambda item: item["id"])

    def _check_parents(self, object_root: Path) -> None:
        current = object_root.parent
        root = self.root.resolve()
        while current.resolve() != root and current != current.parent:
            if (current / OBJECT_NAMASTE).exists():
                raise OcflError(f"Object root cannot be nested under another object at {current}")
            current = current.parent

    def import_directory(
        self,
        identifier: str,
        src_dir: Path,
        message: str,
        on_progress: Callable[[int, int], None],
        cancelled: Callable[[], str | None],
        interrupted: type[Exception],
    ) -> dict:
        self.load()
        object_root = self.object_root(identifier)
        if not object_root.resolve().is_relative_to(self.root.resolve()):
            raise OcflError("Object path escapes the storage root.")
        payload = list_payload(src_dir)
        if not any(path == "ro-crate-metadata.json" for path, _ in payload):
            raise OcflError("The crate directory has no ro-crate-metadata.json.")
        total = sum(path.stat().st_size for _, path in payload)
        exists = (object_root / OBJECT_NAMASTE).is_file()
        inventory = self.read_inventory(object_root) if exists else None
        if inventory and inventory.get("id") != identifier:
            raise OcflError(f"Object at {object_root} belongs to {inventory.get('id')}, not {identifier}.")
        algorithm = inventory.get("digestAlgorithm", "sha512") if inventory else "sha512"
        done = 0

        def bump(count: int) -> None:
            nonlocal done
            done += count
            on_progress(done, total)

        state: dict[str, list[str]] = {}
        sources: dict[str, Path] = {}
        crcs: dict[str, str] = {}
        for logical, path in payload:
            reason = cancelled()
            if reason:
                raise interrupted(reason)
            digest, crc, _ = digest_file(path, algorithm, bump)
            state.setdefault(digest, []).append(logical)
            sources.setdefault(digest, path)
            crcs.setdefault(digest, crc)
        for paths in state.values():
            paths.sort()
        if inventory:
            head = inventory["head"]
            previous = inventory["versions"][head]["state"]
            if {key: sorted(value) for key, value in previous.items()} == state:
                return {"version": head, "created": False, "changed": False, "added": 0, "reused": len(state), "files": len(payload), "bytes": total}
            number = int(re.sub(r"\D", "", head)) + 1
            version = _version_name(head, number)
            manifest = {key: list(value) for key, value in inventory["manifest"].items()}
            fixity = {key: {k: list(v) for k, v in value.items()} for key, value in inventory.get("fixity", {}).items()}
            versions = dict(inventory["versions"])
            content_dir = inventory.get("contentDirectory", "content")
            orphan = object_root / version
            if orphan.exists():
                shutil.rmtree(orphan)
        else:
            version = "v1"
            manifest, fixity, versions = {}, {}, {}
            content_dir = "content"
            self._check_parents(object_root)
            if object_root.exists() and any(object_root.iterdir()):
                raise OcflError(f"{object_root} exists but is not an OCFL object; refusing to overwrite it.")
        stage = self.scratch / f"ocfl-{uuid.uuid4().hex}"
        stage.mkdir(parents=True)
        try:
            version_dir = stage / version
            added = 0
            crc_map = fixity.setdefault("crc32", {})
            for digest, paths in state.items():
                if digest in manifest:
                    continue
                content_path = f"{version}/{content_dir}/{paths[0]}"
                _place(sources[digest], stage / content_path)
                manifest[digest] = [content_path]
                crc_map.setdefault(crcs[digest], []).append(content_path)
                added += 1
            versions[version] = {
                "created": now_iso(),
                "state": state,
                "message": message,
                "user": {"name": "rapid-cdl-data-ingester"},
            }
            new_inventory = {
                "id": identifier,
                "digestAlgorithm": algorithm,
                "type": INVENTORY_TYPE,
                "contentDirectory": content_dir,
                "manifest": manifest,
                "versions": versions,
                "head": version,
                "fixity": fixity,
            }
            version_dir.mkdir(parents=True, exist_ok=True)
            _write_inventory(version_dir, new_inventory)
            if inventory:
                _move_dir(version_dir, object_root / version)
                _write_inventory(object_root, new_inventory)
            else:
                (stage / OBJECT_NAMASTE).write_text("ocfl_object_1.1\n", encoding="utf-8")
                _write_inventory(stage, new_inventory)
                object_root.parent.mkdir(parents=True, exist_ok=True)
                if object_root.exists():
                    object_root.rmdir()
                _move_dir(stage, object_root)
                stage = None
            return {
                "version": version,
                "created": inventory is None,
                "changed": True,
                "added": added,
                "reused": len(state) - added,
                "files": len(payload),
                "bytes": total,
            }
        finally:
            if stage is not None and stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
