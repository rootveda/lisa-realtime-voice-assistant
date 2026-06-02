"""Lisa RAG regression: deterministic FTS retrieval for embedded corpus markers.

Requires generated corpus (see scripts/rag_enterprise_regression/generate_corpus.py) and:
  RAG_ENTERPRISE_REGRESSION=1 WORKSPACE_ROOT=<repo root containing offline_setup/>

Accuracy definition: 100% pass = every marker appears in rag_fts body for the expected file,
and search_knowledge_base returns at least one hit on that path (lexical RAG, not LLM answers).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

KINDS = ("txt", "md", "markdown", "csv", "docx", "xlsx", "xls", "pptx", "pdf")


@pytest.fixture(scope="module")
def lr_enterprise():
    if os.environ.get("RAG_ENTERPRISE_REGRESSION") != "1":
        pytest.skip("Set RAG_ENTERPRISE_REGRESSION=1 to run Lisa RAG corpus tests")
    wr = (os.environ.get("WORKSPACE_ROOT") or "").strip()
    if not wr:
        pytest.skip("WORKSPACE_ROOT must point at the repo root (contains offline_setup/)")
    root = Path(wr).resolve()
    rag_dir = root / "offline_setup" / "rag_data"
    os.environ["LOCAL_RAG_DATA_DIR"] = str(rag_dir)
    import importlib

    import pipecat_bots.local_rag as lr

    importlib.reload(lr)
    return lr


@pytest.fixture(scope="module")
def enterprise_manifest(lr_enterprise):
    p = lr_enterprise.rag_documents_dir() / "enterprise_regression" / "manifest.json"
    if not p.is_file():
        pytest.skip(f"Missing manifest {p}; run generate_corpus.py first")
    return json.loads(p.read_text(encoding="utf-8"))


def _entry(manifest: dict, kind: str) -> dict:
    for f in manifest["files"]:
        if f["kind"] == kind:
            return f
    raise KeyError(kind)


def _assert_marker_indexed(lr, abs_path: Path, marker: str) -> None:
    pstr = str(abs_path.resolve())
    out = lr.search_knowledge_base(marker, limit=24)
    assert out.get("ok") is True, out
    hits = out.get("hits") or []
    assert hits, f"no FTS hits for marker query: {marker[:48]}…"
    paths = {h.get("path") for h in hits}
    assert pstr in paths, f"expected path not in hits: {pstr} got {paths}"

    con = lr._connect()
    try:
        row = con.execute(
            "SELECT 1 FROM rag_fts WHERE path = ? AND body LIKE ? LIMIT 1",
            (pstr, f"%{marker}%"),
        ).fetchone()
    finally:
        con.close()
    assert row is not None, f"marker not found in indexed body for {abs_path.name}"


@pytest.mark.regression
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("case_idx", range(20))
def test_enterprise_corpus_marker_retrieval(lr_enterprise, enterprise_manifest, kind, case_idx):
    lr = lr_enterprise
    ent = _entry(enterprise_manifest, kind)
    marker = ent["markers"][case_idx]
    abs_path = Path(enterprise_manifest["corpus_root"]) / ent["filename"]
    assert abs_path.is_file(), f"missing corpus file {abs_path}"
    _assert_marker_indexed(lr, abs_path, marker)


@pytest.mark.regression
def test_manifest_covers_all_supported_kinds(enterprise_manifest):
    kinds = {f["kind"] for f in enterprise_manifest["files"]}
    assert kinds == set(KINDS)


@pytest.mark.regression
def test_all_files_meet_minimum_size(enterprise_manifest):
    if int(enterprise_manifest.get("target_bytes") or 0) < 50 * 1024 * 1024:
        pytest.skip("manifest built below 50 MiB (use generate_corpus.py default or --target-mib 52)")
    min_b = 50 * 1024 * 1024
    for f in enterprise_manifest["files"]:
        assert int(f["bytes_on_disk"]) >= min_b, (
            f'{f["filename"]}: {f["bytes_on_disk"]} < 50 MiB (regenerate with --target-mib 52)'
        )
