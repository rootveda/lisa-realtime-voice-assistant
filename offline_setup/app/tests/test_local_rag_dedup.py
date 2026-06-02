"""Content-hash FTS dedupe and disk dedup helpers."""

from __future__ import annotations

import importlib
import asyncio

import pytest


@pytest.fixture
def rag_module(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_RAG_DATA_DIR", str(tmp_path))
    import pipecat_bots.local_rag as lr

    importlib.reload(lr)
    return lr


@pytest.mark.regression
def test_fts_dedupe_second_file_shares_canonical(rag_module):
    lr = rag_module
    d = lr.rag_documents_dir()
    body = "chunkable content " * 200 + "\nextra line"
    p1 = d / "alpha.txt"
    p2 = d / "beta.txt"
    p1.write_text(body)
    p2.write_text(body)
    r1 = lr.ingest_file(p1)
    r2 = lr.ingest_file(p2)
    assert r1.get("deduped") is False
    assert r2.get("deduped") is True
    assert r2.get("canonical_path") == str(p1.resolve())
    con = lr._connect()
    try:
        chunks = int(con.execute("SELECT count(*) FROM rag_fts").fetchone()[0])
        inst = int(con.execute("SELECT count(*) FROM rag_doc_instance").fetchone()[0])
    finally:
        con.close()
    assert inst == 2
    assert chunks == pytest.approx(len(lr._chunk_text(body)), abs=2)


@pytest.mark.regression
def test_store_dedup_bytes_single_blob(rag_module):
    lr = rag_module
    raw = b"same-bytes"
    a, new1 = lr.store_dedup_bytes(raw, ".txt")
    b, new2 = lr.store_dedup_bytes(raw, ".txt")
    assert a == b
    assert new1 is True
    assert new2 is False


@pytest.mark.regression
def test_remove_symlink_catalog_and_dedup_path(rag_module):
    lr = rag_module
    d = lr.rag_documents_dir()
    body = "delete me via symlink " * 40
    dest = d / "symlink_target.txt"
    info = lr.write_dedup_linked_file(dest, body.encode(), suffix=".txt")
    assert dest.is_symlink()
    lr.ingest_file(dest)
    dedup_path = info["dedup_blob"]

    listed = lr.rag_files_detail(limit=50)["files"]
    paths = {row["path"] for row in listed}
    assert str(dest.absolute()) in paths
    assert dedup_path not in paths

    out_dedup = lr.remove_rag_file(dedup_path)
    assert out_dedup.get("ok") is True
    assert not dest.exists()

    dest2 = d / "symlink_target2.txt"
    lr.write_dedup_linked_file(dest2, body.encode(), suffix=".txt")
    lr.ingest_file(dest2)
    out_cat = lr.remove_rag_file(str(dest2.absolute()))
    assert out_cat.get("ok") is True
    assert not dest2.exists()


@pytest.mark.regression
def test_legacy_migration_merges_duplicate_fts_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_RAG_DATA_DIR", str(tmp_path))
    import pipecat_bots.local_rag as lr

    importlib.reload(lr)
    lr.init_schema()
    d = lr.rag_documents_dir()
    body = "migration merge text " * 150
    p1 = d / "m1.txt"
    p2 = d / "m2.txt"
    p1.write_text(body)
    p2.write_text(body)
    p1s, p2s = str(p1.resolve()), str(p2.resolve())
    con = lr._connect()
    try:
        chunks = lr._chunk_text(body)
        for i, c in enumerate(chunks):
            con.execute(
                "INSERT INTO rag_fts(path, chunk_idx, body) VALUES (?, ?, ?)",
                (p1s, i, c),
            )
        for i, c in enumerate(chunks):
            con.execute(
                "INSERT INTO rag_fts(path, chunk_idx, body) VALUES (?, ?, ?)",
                (p2s, i, c),
            )
        con.commit()
        n_before = int(con.execute("SELECT count(*) FROM rag_fts").fetchone()[0])
        assert n_before == 2 * len(chunks)
    finally:
        con.close()

    importlib.reload(lr)
    lr.init_schema()
    con = lr._connect()
    try:
        n_after = int(con.execute("SELECT count(*) FROM rag_fts").fetchone()[0])
        inst = int(con.execute("SELECT count(*) FROM rag_doc_instance").fetchone()[0])
    finally:
        con.close()
    assert n_after == len(chunks)
    assert inst == 2


@pytest.mark.regression
def test_remove_rag_file_deletes_disk_and_registry(rag_module):
    lr = rag_module
    d = lr.rag_documents_dir()
    p = d / "deleteme.txt"
    p.write_text("remove this content")
    ing = lr.ingest_file(p)
    assert ing.get("ok") is True
    out = lr.remove_rag_file(str(p))
    assert out.get("ok") is True
    assert out.get("removed_disk") is True
    assert not p.exists()
    con = lr._connect()
    try:
        fts_n = int(con.execute("SELECT count(*) FROM rag_fts WHERE path = ?", (str(p.resolve()),)).fetchone()[0])
        inst_n = int(
            con.execute("SELECT count(*) FROM rag_doc_instance WHERE path = ?", (str(p.resolve()),)).fetchone()[0]
        )
    finally:
        con.close()
    assert fts_n == 0
    assert inst_n == 0


@pytest.mark.regression
def test_remove_rag_file_symlink_path_allowed(rag_module):
    lr = rag_module
    docs = lr.rag_documents_dir()
    link = docs / "symlink_doc.txt"
    lr.write_dedup_linked_file(link, b"symlink body", suffix=".txt")
    ing = lr.ingest_file(link)
    assert ing.get("ok") is True
    assert str(link.absolute()).startswith(str(docs.absolute()))
    out = lr.remove_rag_file(str(link))
    assert out.get("ok") is True
    assert not link.exists()


@pytest.mark.regression
def test_chat_attachment_mirror_uses_content_hash_name(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_RAG_DATA_DIR", str(tmp_path))
    import pipecat_bots.local_rag as lr
    import pipecat_bots.chat_attachments as ca

    importlib.reload(lr)
    importlib.reload(ca)
    sid = "sess1"
    payload = b"same bytes"
    files = [("doc.txt", payload, "text/plain"), ("doc.txt", payload, "text/plain")]
    asyncio.run(ca.save_uploads(sid, files))
    d = lr.rag_from_chat_dir() / sid
    txts = sorted([p for p in d.iterdir() if p.name.endswith("_doc.txt")])
    # Dedup in from_chat should keep one logical mirror path for identical uploads.
    assert len(txts) == 1
