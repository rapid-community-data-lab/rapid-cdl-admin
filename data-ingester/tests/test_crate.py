import copy

import pytest

from app import crate
from conftest import docsearch_metadata


def test_normalise_docsearch_shape(tmp_path):
    metadata = docsearch_metadata(3)
    report = crate.normalise(metadata, None, "sample")
    graph = {entity["@id"]: entity for entity in metadata["@graph"]}
    assert report["rootId"] == "arcp://name,sample"
    assert graph["ro-crate-metadata.json"]["about"] == {"@id": "arcp://name,sample"}
    root = graph["arcp://name,sample"]
    assert "RepositoryCollection" in root["@type"]
    assert {"@id": crate.LDAC_COLLECTION} in crate.as_list(root["conformsTo"])
    member = graph["arcp://name,sample/inquiry-1"]
    assert {"@id": crate.LDAC_OBJECT} in crate.as_list(member["conformsTo"])
    assert member["isPartOf"] == {"@id": "arcp://name,sample"}
    assert graph["sample/R001/doc.pdf"]["isPartOf"] == {"@id": "arcp://name,sample/inquiry-1"}
    assert {"@id": "arcp://name,sample/inquiry-0"} in root["hasPart"]
    assert "./" not in graph and "#inquiry-0" not in graph


def test_custom_name_and_existing_iri():
    metadata = docsearch_metadata(1)
    crate.normalise(metadata, "alrc", "ignored")
    again = copy.deepcopy(metadata)
    report = crate.normalise(again, None, "other")
    assert report["rootId"] == "arcp://name,alrc"
    assert report["changes"] == []
    renamed = crate.normalise(copy.deepcopy(metadata), "alrc-v2", "other")
    assert renamed["rootId"] == "arcp://name,alrc-v2"


def test_analyse_expected_members_and_missing(tmp_path):
    metadata = docsearch_metadata(2)
    report = crate.normalise(metadata, None, "sample")
    (tmp_path / "sample" / "R000").mkdir(parents=True)
    (tmp_path / "sample" / "R000" / "doc.pdf.txt").write_text("x")
    analysis = crate.analyse(metadata, report["rootId"], tmp_path)
    assert analysis["expectedMembers"] == 2
    assert analysis["counts"]["objects"] == 2
    assert analysis["counts"]["files"] == 4
    assert analysis["filesPresent"] == 1
    assert len(analysis["missing"]) == 3
    assert analysis["expectedByParent"] == {
        "arcp://name,sample": 2,
        "arcp://name,sample/inquiry-0": 2,
        "arcp://name,sample/inquiry-1": 2,
    }


def test_text_payload_selection():
    keep = crate.text_payload(docsearch_metadata(2))
    assert keep == {"sample/R000/doc.pdf.txt", "sample/R001/doc.pdf.txt"}


@pytest.mark.parametrize("value", ["bad name", "a/b", "", "-x", "x" * 129])
def test_validate_name_rejects(value):
    with pytest.raises(crate.CrateError):
        crate.validate_name(value)


def test_validate_name_accepts_prefixed():
    assert crate.validate_name("arcp://name,alrc") == "alrc"


def test_default_name_prefers_single_payload_folder(tmp_path):
    (tmp_path / "alrc").mkdir()
    assert crate.default_name(tmp_path, tmp_path, {}, []) == "alrc"
    (tmp_path / "other").mkdir()
    assert crate.default_name(tmp_path, tmp_path, {}, ["My Crate.zip.001"]) == "my-crate"
