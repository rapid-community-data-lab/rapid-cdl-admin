import json
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote

LDAC_COLLECTION = "https://w3id.org/ldac/profile#Collection"
LDAC_OBJECT = "https://w3id.org/ldac/profile#Object"
ROCRATE_12_CONTEXT = "https://w3id.org/ro/crate/1.2/context"
METADATA_NAME = "ro-crate-metadata.json"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
INDEX_PROFILES = {
    "RepositoryCollection": LDAC_COLLECTION,
    "RepositoryObject": LDAC_OBJECT,
    "File": "",
    "Person": "https://w3id.org/ldac/profile#Person",
    "Organization": "https://w3id.org/ldac/profile#Organization",
    "SoftwareApplication": "https://w3id.org/ldac/profile#SoftwareApplication",
}
EXTRACTED_TEXT_PROPERTIES = ("ds:extractedText", "https://docu-search.internal/terms#extractedText", "pc:extractedText")


class CrateError(Exception):
    pass


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def ref_ids(value) -> list[str]:
    return [item["@id"] for item in as_list(value) if isinstance(item, dict) and isinstance(item.get("@id"), str)]


def is_iri(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value or ""))


def slugify(text: str | None) -> str:
    value = re.sub(r"[^A-Za-z0-9._~-]+", "-", (text or "").strip()).strip("-._~")
    value = re.sub(r"-{2,}", "-", value).lower()
    return value[:128]


def validate_name(name: str) -> str:
    candidate = (name or "").strip()
    if candidate.startswith("arcp://name,"):
        candidate = candidate[len("arcp://name,"):]
    if not NAME_PATTERN.match(candidate):
        raise CrateError(
            "The collection identifier may only use letters, digits and . _ ~ - (up to 128 characters, no spaces or slashes)."
        )
    return candidate


def load_metadata(crate_dir: Path) -> dict:
    path = crate_dir / METADATA_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise CrateError(f"{METADATA_NAME} is missing in {crate_dir.name or 'the archive root'}.") from exc
    except json.JSONDecodeError as exc:
        raise CrateError(f"{METADATA_NAME} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("@graph"), list):
        raise CrateError(f"{METADATA_NAME} has no @graph.")
    return data


def find_descriptor(graph: list) -> dict:
    for entity in graph:
        if isinstance(entity, dict) and entity.get("@id") == METADATA_NAME:
            return entity
    for entity in graph:
        if isinstance(entity, dict) and str(entity.get("@id", "")).endswith("/" + METADATA_NAME):
            return entity
    raise CrateError("The crate has no metadata descriptor (an entity with @id ro-crate-metadata.json).")


def remap_refs(node, id_map: dict) -> None:
    if isinstance(node, dict):
        if set(node.keys()) == {"@id"} and node["@id"] in id_map:
            node["@id"] = id_map[node["@id"]]
            return
        for value in node.values():
            remap_refs(value, id_map)
    elif isinstance(node, list):
        for value in node:
            remap_refs(value, id_map)


def _member_slug(entity_id: str) -> str:
    raw = entity_id[1:] if entity_id.startswith("#") else entity_id
    raw = raw.strip().strip("/")
    if raw.startswith("./"):
        raw = raw[2:]
    return quote(raw, safe="-._~/:,()@!$&'*+;=")


def _add_profile(entity: dict, profile: str) -> bool:
    current = as_list(entity.get("conformsTo"))
    if profile in ref_ids(current):
        return False
    current.append({"@id": profile})
    entity["conformsTo"] = current[0] if len(current) == 1 else current
    return True


def _ensure_types(entity: dict, required: list[str]) -> bool:
    types = as_list(entity.get("@type"))
    changed = False
    for item in required:
        if item not in types:
            types.append(item)
            changed = True
    entity["@type"] = types
    return changed


def default_name(crate_dir: Path, extract_root: Path, root_entity: dict, archive_names: list[str]) -> str:
    if crate_dir.resolve() != extract_root.resolve():
        candidate = slugify(crate_dir.name)
        if candidate:
            return candidate
    folders = sorted(path.name for path in crate_dir.iterdir() if path.is_dir() and not path.name.startswith("."))
    if len(folders) == 1 and slugify(folders[0]):
        return slugify(folders[0])
    for name in archive_names:
        stem = re.sub(r"(\.zip)?(\.z\d+|\.\d{3}|\.zip)$", "", (name or "").lower())
        stem = re.sub(r"[-_.]?(part)?\d+$", "", stem)
        if slugify(stem):
            return slugify(stem)
    names = as_list(root_entity.get("name"))
    if names and slugify(str(names[0])):
        return slugify(str(names[0]))
    raise CrateError("A collection identifier could not be derived; enter one in the form.")


def normalise(metadata: dict, name: str | None, fallback: str) -> dict:
    graph = metadata["@graph"]
    by_id = {entity.get("@id"): entity for entity in graph if isinstance(entity, dict)}
    descriptor = find_descriptor(graph)
    about = ref_ids(descriptor.get("about"))
    if not about:
        raise CrateError("The metadata descriptor has no about.@id pointing at the root dataset.")
    old_root = about[0]
    root = by_id.get(old_root)
    if root is None:
        raise CrateError(f"The root dataset {old_root} is not in the @graph.")
    warnings: list[str] = []
    changes: list[str] = []
    if name:
        root_id = f"arcp://name,{validate_name(name)}"
    elif old_root.startswith("arcp://"):
        root_id = old_root
    else:
        root_id = f"arcp://name,{validate_name(fallback)}"
    id_map: dict[str, str] = {}
    if root_id != old_root:
        id_map[old_root] = root_id
        changes.append(f"root {old_root} -> {root_id}")
    renamed_iri_root = is_iri(old_root) and root_id != old_root
    for entity in graph:
        if not isinstance(entity, dict) or entity is root:
            continue
        entity_id = entity.get("@id")
        if not isinstance(entity_id, str):
            continue
        types = as_list(entity.get("@type"))
        if renamed_iri_root and entity_id.startswith(old_root + "/"):
            id_map[entity_id] = root_id + entity_id[len(old_root):]
            continue
        if ("RepositoryObject" in types or "RepositoryCollection" in types) and not is_iri(entity_id):
            slug = _member_slug(entity_id)
            if not slug:
                raise CrateError(f"Member {entity_id} has an empty identifier.")
            id_map[entity_id] = f"{root_id}/{slug}"
    targets = list(id_map.values())
    if len(set(targets)) != len(targets):
        raise CrateError("Two members map to the same identifier; give them distinct @id values.")
    clashes = [target for target in targets if target in by_id and target not in id_map]
    if clashes:
        raise CrateError(f"Identifier {clashes[0]} is already used by another entity.")
    member_changes = sum(1 for key in id_map if key != old_root)
    if member_changes:
        changes.append(f"{member_changes} member identifiers rewritten under {root_id}")
    for entity in graph:
        if not isinstance(entity, dict):
            continue
        if entity.get("@id") in id_map:
            entity["@id"] = id_map[entity["@id"]]
        remap_refs({key: value for key, value in entity.items() if key != "@id"}, id_map)
    descriptor["about"] = {"@id": root_id}
    root_types = as_list(root.get("@type"))
    if "RepositoryObject" in root_types:
        if _ensure_types(root, ["Dataset"]):
            changes.append("root typed Dataset")
        if _add_profile(root, LDAC_OBJECT):
            changes.append("root conformsTo LDaC Object profile")
    else:
        if _ensure_types(root, ["Dataset", "RepositoryCollection"]):
            changes.append("root typed Dataset + RepositoryCollection")
        if _add_profile(root, LDAC_COLLECTION):
            changes.append("root conformsTo LDaC Collection profile")
    added_object, added_collection = 0, 0
    for entity in graph:
        if not isinstance(entity, dict) or entity is root:
            continue
        types = as_list(entity.get("@type"))
        if "RepositoryObject" in types and _add_profile(entity, LDAC_OBJECT):
            added_object += 1
        elif "RepositoryCollection" in types and _add_profile(entity, LDAC_COLLECTION):
            added_collection += 1
    if added_object:
        changes.append(f"{added_object} objects conformsTo LDaC Object profile")
    if added_collection:
        changes.append(f"{added_collection} sub-collections conformsTo LDaC Collection profile")
    if not root.get("license"):
        warnings.append("The root dataset has no license; the API default (CC-BY-4.0) will apply.")
    contexts = [item for item in as_list(metadata.get("@context")) if isinstance(item, str)]
    if ROCRATE_12_CONTEXT not in contexts:
        warnings.append(
            "The crate does not use the RO-Crate 1.2 context; entity types may fall back to profile IRIs."
        )
    return {"rootId": root_id, "previousRootId": old_root, "changes": changes, "warnings": warnings}


def entity_types(entity: dict) -> list[str]:
    return [item for item in as_list(entity.get("@type")) if isinstance(item, str)]


def indexable(entity: dict) -> str | None:
    for kind in entity_types(entity):
        if kind in INDEX_PROFILES:
            profile = INDEX_PROFILES[kind]
            if profile and profile not in ref_ids(entity.get("conformsTo")):
                return None
            return kind
    return None


def member_of(entity: dict, reverse: dict, by_id: dict) -> str | None:
    def is_object(identifier: str) -> bool:
        target = by_id.get(identifier)
        return bool(target) and "RepositoryObject" in entity_types(target)

    entity_id = entity.get("@id")
    for candidate in (
        ref_ids(entity.get("pcdm:memberOf"))[:1],
        ref_ids(entity.get("memberOf"))[:1],
        reverse.get(("pcdm:hasMember", entity_id), [])[:1],
        reverse.get(("hasMember", entity_id), [])[:1],
        [item for item in ref_ids(entity.get("isPartOf")) if is_object(item)][:1],
        [item for item in reverse.get(("hasPart", entity_id), []) if is_object(item)][:1],
        ref_ids(entity.get("isPartOf"))[:1],
        reverse.get(("hasPart", entity_id), [])[:1],
    ):
        if candidate:
            return candidate[0]
    return None


def analyse(metadata: dict, root_id: str, crate_dir: Path) -> dict:
    graph = [entity for entity in metadata["@graph"] if isinstance(entity, dict)]
    by_id = {entity.get("@id"): entity for entity in graph}
    reverse: dict[tuple[str, str], list[str]] = {}
    for entity in graph:
        for prop in ("pcdm:hasMember", "hasMember", "hasPart"):
            for target in ref_ids(entity.get(prop)):
                reverse.setdefault((prop, target), []).append(entity.get("@id"))
    counts = {"collections": 0, "objects": 0, "files": 0, "other": 0, "skipped": 0}
    members = 0
    files_local, files_present, missing = 0, 0, []
    by_parent: dict[str, int] = {}
    for entity in graph:
        kind = indexable(entity)
        types = entity_types(entity)
        if kind is None:
            if any(item in INDEX_PROFILES for item in types):
                counts["skipped"] += 1
            continue
        parent = member_of(entity, reverse, by_id) if entity.get("@id") != root_id else None
        if parent and is_iri(parent) and not parent.startswith("#"):
            by_parent[parent] = by_parent.get(parent, 0) + 1
        if kind == "RepositoryCollection":
            counts["collections"] += 1
        elif kind == "RepositoryObject":
            counts["objects"] += 1
        elif kind == "File":
            counts["files"] += 1
        else:
            counts["other"] += 1
        if entity.get("@id") != root_id and member_of(entity, reverse, by_id) == root_id:
            members += 1
        if kind == "File":
            file_id = entity.get("@id", "")
            if is_iri(file_id):
                continue
            files_local += 1
            relative = file_id[2:] if file_id.startswith("./") else file_id
            if (crate_dir / relative).is_file() or (crate_dir / unquote(relative)).is_file():
                files_present += 1
            elif len(missing) < 2000:
                missing.append(file_id)
    return {
        "counts": counts,
        "expectedMembers": members,
        "filesLocal": files_local,
        "filesPresent": files_present,
        "missing": missing,
        "expectedByParent": by_parent,
    }


def text_payload(metadata: dict) -> set[str]:
    keep = set()
    for entity in metadata.get("@graph", []):
        if not isinstance(entity, dict) or "File" not in entity_types(entity):
            continue
        file_id = entity.get("@id", "")
        if not isinstance(file_id, str) or is_iri(file_id):
            continue
        formats = [str(item) for item in as_list(entity.get("encodingFormat")) if isinstance(item, str)]
        if any(item.startswith("text/") for item in formats) or file_id.lower().endswith(".txt"):
            keep.add(file_id[2:] if file_id.startswith("./") else file_id)
    for entity in metadata.get("@graph", []):
        if not isinstance(entity, dict):
            continue
        for prop in EXTRACTED_TEXT_PROPERTIES:
            for target in ref_ids(entity.get(prop)):
                if not is_iri(target):
                    keep.add(target[2:] if target.startswith("./") else target)
    return keep | {unquote(item) for item in keep}


def write_metadata(crate_dir: Path, metadata: dict) -> None:
    path = crate_dir / METADATA_NAME
    temp = crate_dir / (METADATA_NAME + ".tmp")
    temp.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)
