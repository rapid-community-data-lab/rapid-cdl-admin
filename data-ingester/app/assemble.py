import bisect
import io
import os
import re
import stat
import struct
import zipfile
from collections.abc import Callable
from pathlib import Path

LOCAL_SIG = b"PK\x03\x04"
SPAN_SIG = b"PK\x07\x08"
EOCD_SIG = b"PK\x05\x06"
ZIP64_LOCATOR_SIG = b"PK\x06\x07"
METADATA_NAME = "ro-crate-metadata.json"
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
COPY_CHUNK = 1024 * 1024


class AssembleError(Exception):
    pass


class ConcatReader(io.RawIOBase):
    def __init__(self, paths: list[Path]):
        super().__init__()
        self._files = [open(path, "rb") for path in paths]
        self._sizes = [os.fstat(handle.fileno()).st_size for handle in self._files]
        self._starts = []
        position = 0
        for size in self._sizes:
            self._starts.append(position)
            position += size
        self._size = position
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._pos + offset
        elif whence == io.SEEK_END:
            target = self._size + offset
        else:
            raise ValueError("invalid whence")
        if target < 0:
            raise OSError("negative seek position")
        self._pos = target
        return self._pos

    def readinto(self, buffer) -> int:
        view = memoryview(buffer).cast("B")
        filled = 0
        while filled < len(view) and self._pos < self._size:
            index = bisect.bisect_right(self._starts, self._pos) - 1
            handle = self._files[index]
            local = self._pos - self._starts[index]
            handle.seek(local)
            want = min(len(view) - filled, self._sizes[index] - local)
            got = handle.readinto(view[filled:filled + want])
            if not got:
                break
            filled += got
            self._pos += got
        return filled

    def close(self) -> None:
        for handle in self._files:
            handle.close()
        super().close()


def open_source(paths: list[Path]):
    if len(paths) == 1:
        return open(paths[0], "rb")
    return io.BufferedReader(ConcatReader(paths), buffer_size=COPY_CHUNK)


def _eocd_info(path: Path) -> dict | None:
    size = path.stat().st_size
    with open(path, "rb") as handle:
        start = max(0, size - (65535 + 22 + 20))
        handle.seek(start)
        tail = handle.read()
    index = tail.rfind(EOCD_SIG)
    if index < 0 or len(tail) - index < 22:
        return None
    disk, cd_disk, entries_disk, entries_total, cd_size, cd_offset, comment_len = struct.unpack(
        "<HHHHIIH", tail[index + 4:index + 22]
    )
    info = {"disk": disk, "cdDisk": cd_disk, "entries": entries_total, "disks": disk + 1}
    locator = tail.rfind(ZIP64_LOCATOR_SIG, 0, index)
    if locator >= 0 and index - locator == 20:
        _, zip64_disk, _, total_disks = struct.unpack("<IIQI", tail[locator:locator + 20])
        info["disks"] = max(info["disks"], total_disks)
        if disk == 0xFFFF:
            info["disk"] = total_disks - 1
    return info


def _head(path: Path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(4)


def _validate_zip(paths: list[Path]) -> int:
    with open_source(paths) as source, zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        for info in members:
            with archive.open(info) as entry:
                entry.read(0)
        return len(members)


def _try_zip(paths: list[Path]) -> bool:
    try:
        _validate_zip(paths)
        return True
    except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, RuntimeError, ValueError, struct.error):
        return False


def plan(paths: list[Path], names: list[str | None]) -> dict:
    if not paths:
        raise AssembleError("No files to assemble.")
    heads = [_head(path) for path in paths]
    eocds = [_eocd_info(path) for path in paths]
    if len(paths) == 1:
        if eocds[0] and eocds[0]["disk"] > 0:
            raise AssembleError(
                f"This is the last part of a split zip of {eocds[0]['disks']} parts; add the other parts too."
            )
        if heads[0] == SPAN_SIG:
            raise AssembleError("This is the first part of a split zip; add the remaining parts too.")
        if not eocds[0]:
            raise AssembleError("The file is not a zip archive, or it is one part of a split archive.")
        if not _try_zip(paths):
            raise AssembleError("The zip archive is damaged or incomplete.")
        return {"kind": "single", "groups": [[0]]}
    standalone = [heads[i] == LOCAL_SIG and eocds[i] is not None and eocds[i]["disk"] == 0 for i in range(len(paths))]
    if all(standalone) and all(_try_zip([path]) for path in paths):
        return {"kind": "multi", "groups": [[i] for i in range(len(paths))]}
    spanned = [i for i, info in enumerate(eocds) if info and info["disk"] > 0]
    if spanned or SPAN_SIG in heads:
        if not spanned:
            raise AssembleError("The last part of the split zip (the .zip file) is missing.")
        last = spanned[0]
        expected = eocds[last]["disks"]
        if expected != len(paths):
            raise AssembleError(f"The split zip has {expected} parts but {len(paths)} were provided.")
        others = [i for i in range(len(paths)) if i != last]
        if all(names[i] for i in others):
            others.sort(key=lambda i: _part_number(names[i]))
        return {"kind": "spanned", "order": others + [last]}
    candidates = [list(range(len(paths)))]
    if all(names):
        ordered = sorted(range(len(paths)), key=lambda i: _part_number(names[i]))
        if ordered != candidates[0]:
            candidates.append(ordered)
    for order in candidates:
        if _try_zip([paths[i] for i in order]):
            return {"kind": "concat", "groups": [order]}
    raise AssembleError(
        "The parts do not form a valid zip archive. Check that every part is present and listed in order."
    )


def _part_number(name: str | None) -> tuple:
    text = (name or "").lower()
    match = re.search(r"\.z(\d+)$", text)
    if match:
        return (0, int(match.group(1)), text)
    if text.endswith(".zip"):
        return (1, 0, text)
    match = re.search(r"(\d+)(?!.*\d)", text)
    if match:
        return (0, int(match.group(1)), text)
    return (2, 0, text)


def _read_at(stream: ConcatReader, position: int, length: int) -> bytes:
    stream.seek(position)
    data = stream.read(length)
    if data is None or len(data) != length:
        raise AssembleError("The split zip is truncated.")
    return data


def _spanned_directory(stream: ConcatReader, sizes: list[int], starts: list[int]) -> tuple[int, int, int]:
    tail_length = min(sizes[-1], 65535 + 22 + 20)
    tail = _read_at(stream, starts[-1] + sizes[-1] - tail_length, tail_length)
    index = tail.rfind(EOCD_SIG)
    if index < 0 or len(tail) - index < 22:
        raise AssembleError("The last part of the split zip has no end-of-archive record.")
    _, cd_disk, _, entries, cd_size, cd_offset, _ = struct.unpack("<HHHHIIH", tail[index + 4:index + 22])
    if index >= 20 and tail[index - 20:index - 16] == ZIP64_LOCATOR_SIG:
        _, record_disk, record_offset, _ = struct.unpack("<IIQI", tail[index - 20:index])
        if record_disk >= len(starts):
            raise AssembleError("The split zip points at a part that was not provided.")
        record = _read_at(stream, starts[record_disk] + record_offset, 56)
        if record[:4] != b"PK\x06\x06":
            raise AssembleError("The split zip has a damaged ZIP64 record.")
        _, cd_disk, _, entries, cd_size, cd_offset = struct.unpack("<IIQQQQ", record[16:56])
    if cd_disk >= len(starts):
        raise AssembleError("The split zip points at a part that was not provided.")
    return starts[cd_disk] + cd_offset, cd_size, entries


def _rewrite_entry(directory: bytes, position: int, starts: list[int], skip: int) -> tuple[bytes, int, bool]:
    if directory[position:position + 4] != b"PK\x01\x02":
        raise AssembleError("The split zip has a damaged central directory.")
    fixed = bytearray(directory[position:position + 46])
    compressed, uncompressed, name_length, extra_length, comment_length, disk = struct.unpack("<IIHHHH", fixed[20:36])
    relative = struct.unpack("<I", fixed[42:46])[0]
    name_end = position + 46 + name_length
    extra = directory[name_end:name_end + extra_length]
    comment = directory[name_end + extra_length:name_end + extra_length + comment_length]
    blocks, zip64 = [], b""
    cursor = 0
    while cursor + 4 <= len(extra):
        header, size = struct.unpack("<HH", extra[cursor:cursor + 4])
        body = extra[cursor + 4:cursor + 4 + size]
        if header == 0x0001:
            zip64 = body
        else:
            blocks.append((header, body))
        cursor += 4 + size
    values = list(struct.unpack(f"<{len(zip64) // 8}Q", zip64[: (len(zip64) // 8) * 8])) if zip64 else []
    taken = 0
    sizes_body = b""
    for field in (uncompressed, compressed):
        if field == 0xFFFFFFFF:
            if taken >= len(values):
                raise AssembleError("The split zip has a damaged ZIP64 entry.")
            sizes_body += struct.pack("<Q", values[taken])
            taken += 1
    if relative == 0xFFFFFFFF:
        if taken >= len(values):
            raise AssembleError("The split zip has a damaged ZIP64 entry.")
        relative = values[taken]
        taken += 1
    if disk == 0xFFFF:
        disk = struct.unpack("<I", zip64[taken * 8:taken * 8 + 4])[0]
    if disk >= len(starts):
        raise AssembleError("The split zip points at a part that was not provided.")
    absolute = starts[disk] + relative - skip
    needs64 = absolute >= 0xFFFFFFFF
    body = sizes_body + (struct.pack("<Q", absolute) if needs64 else b"")
    fixed[34:36] = struct.pack("<H", 0)
    fixed[42:46] = struct.pack("<I", 0xFFFFFFFF if needs64 else absolute)
    new_extra = (struct.pack("<HH", 0x0001, len(body)) + body if body else b"") + b"".join(
        struct.pack("<HH", header, len(content)) + content for header, content in blocks
    )
    fixed[30:32] = struct.pack("<H", len(new_extra))
    record = bytes(fixed) + directory[position + 46:name_end] + new_extra + comment
    return record, 46 + name_length + extra_length + comment_length, needs64


def join_spanned(paths: list[Path], workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    sizes = [path.stat().st_size for path in paths]
    starts = [sum(sizes[:index]) for index in range(len(sizes))]
    output = workdir / "joined.zip"
    output.unlink(missing_ok=True)
    with ConcatReader(paths) as stream:
        cd_start, cd_size, entries = _spanned_directory(stream, sizes, starts)
        directory = _read_at(stream, cd_start, cd_size)
        skip = 4 if _read_at(stream, 0, 4) in (SPAN_SIG, b"PK00") else 0
        records, position, needs64 = [], 0, False
        for _ in range(entries):
            record, consumed, wide = _rewrite_entry(directory, position, starts, skip)
            records.append(record)
            position += consumed
            needs64 = needs64 or wide
        new_directory = b"".join(records)
        data_length = cd_start - skip
        with open(output, "wb") as writer:
            stream.seek(skip)
            remaining = data_length
            while remaining > 0:
                chunk = stream.read(min(COPY_CHUNK, remaining))
                if not chunk:
                    raise AssembleError("The split zip is truncated.")
                writer.write(chunk)
                remaining -= len(chunk)
            writer.write(new_directory)
            directory_end = data_length + len(new_directory)
            if needs64 or entries >= 0xFFFF or data_length >= 0xFFFFFFFF or len(new_directory) >= 0xFFFFFFFF:
                writer.write(
                    struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, entries, entries, len(new_directory), data_length)
                )
                writer.write(struct.pack("<IIQI", 0x07064B50, 0, directory_end, 1))
            writer.write(
                struct.pack(
                    "<IHHHHIIH",
                    0x06054B50,
                    0,
                    0,
                    min(entries, 0xFFFF),
                    min(entries, 0xFFFF),
                    min(len(new_directory), 0xFFFFFFFF),
                    min(data_length, 0xFFFFFFFF),
                    0,
                )
            )
    if not _try_zip([output]):
        output.unlink(missing_ok=True)
        raise AssembleError("The split zip could not be joined into a valid archive.")
    return output


def member_path(name: str) -> str | None:
    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        return None
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return None
    if not parts:
        return ""
    if parts[0] == "__MACOSX" or parts[-1] in JUNK_NAMES or parts[-1].startswith("._"):
        return ""
    return "/".join(parts)


def list_members(sources: list[list[Path]]) -> list[dict]:
    members = []
    for group_index, group in enumerate(sources):
        with open_source(group) as source, zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                members.append(
                    {
                        "group": group_index,
                        "name": info.filename,
                        "path": member_path(info.filename),
                        "dir": info.is_dir(),
                        "size": info.file_size,
                        "encrypted": bool(info.flag_bits & 0x1),
                        "symlink": stat.S_ISLNK(info.external_attr >> 16),
                    }
                )
    return members


def crate_roots(members: list[dict]) -> list[str]:
    roots = sorted(
        {
            member["path"].rsplit("/", 1)[0] if "/" in member["path"] else ""
            for member in members
            if member["path"] and not member["dir"] and member["path"].rsplit("/", 1)[-1] == METADATA_NAME
        },
        key=lambda value: (value.count("/"), value),
    )
    selected: list[str] = []
    for root in roots:
        if any(root == parent or (parent == "" and root != "") or root.startswith(parent + "/") for parent in selected):
            continue
        selected.append(root)
    return selected


def extract(
    sources: list[list[Path]],
    dest: Path,
    keep: Callable[[str], bool],
    on_progress: Callable[[int], None],
    cancelled: Callable[[], str | None],
    interrupted: type[Exception],
) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    base = dest.resolve()
    stats = {"files": 0, "bytes": 0, "skipped": 0, "duplicates": 0, "unsafe": [], "symlinks": 0}
    for group in sources:
        with open_source(group) as source, zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                relative = member_path(info.filename)
                if relative is None:
                    stats["unsafe"].append(info.filename[:200])
                    continue
                if relative == "":
                    continue
                target = dest / relative
                if not target.resolve().is_relative_to(base):
                    stats["unsafe"].append(info.filename[:200])
                    continue
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if stat.S_ISLNK(info.external_attr >> 16):
                    stats["symlinks"] += 1
                    continue
                if info.flag_bits & 0x1:
                    raise AssembleError(f"Encrypted zip entries are not supported: {info.filename[:200]}")
                if not keep(relative):
                    stats["skipped"] += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    stats["duplicates"] += 1
                    target.unlink()
                with archive.open(info) as reader, open(target, "wb") as writer:
                    while True:
                        reason = cancelled()
                        if reason:
                            raise interrupted(reason)
                        chunk = reader.read(COPY_CHUNK)
                        if not chunk:
                            break
                        writer.write(chunk)
                        stats["bytes"] += len(chunk)
                        on_progress(stats["bytes"])
                os.chmod(target, 0o644)
                stats["files"] += 1
    return stats


def read_member(sources: list[list[Path]], group: int, name: str, limit: int = 512 * 1024 ** 2) -> bytes:
    with open_source(sources[group]) as source, zipfile.ZipFile(source) as archive:
        info = archive.getinfo(name)
        if info.file_size > limit:
            raise AssembleError(f"{name} is too large to read ({info.file_size} bytes).")
        return archive.read(info)
