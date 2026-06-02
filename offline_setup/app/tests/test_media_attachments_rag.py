"""Media (image/audio/video) attachments, documents-folder RAG, and chat-mode ordering.

Voice / voice+video / voice+video+face share the same HTTP attachment path when the user
sends a **text** message with **Attach** — only the vision augmentation step differs.
Voice **utterances** use ``augment_voice_transcript_with_rag`` over the same FTS index.
"""

from __future__ import annotations

import asyncio
import importlib
import io

import pytest

pytest.importorskip("PIL", reason="Pillow required for image attachment tests")


def _fake_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "docs" / "instructions").mkdir(parents=True)
    (root / "offline_setup" / "rag_data" / "attachments").mkdir(parents=True)
    return root


def _minimal_png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 5), (9, 10, 11)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def isolated_rag_workspace(tmp_path, monkeypatch):
    """Isolate repo root + RAG DB under tmp_path (no writes to real workspace)."""
    ws = _fake_workspace(tmp_path)
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    rag_base = tmp_path / "rag_store"
    monkeypatch.setenv("LOCAL_RAG_DATA_DIR", str(rag_base))
    import pipecat_bots.local_rag as lr
    import pipecat_bots.chat_attachments as ca

    importlib.reload(lr)
    importlib.reload(ca)
    lr.init_schema()
    return {"lr": lr, "ca": ca, "ws": ws, "rag_base": rag_base}


@pytest.mark.regression
def test_chat_upload_png_and_audio_extracted_text(isolated_rag_workspace):
    ca = isolated_rag_workspace["ca"]
    png = _minimal_png()
    mp3ish = b"\xff\xfb\x90\x00" + b"\x00" * 128

    async def run():
        return await ca.save_uploads(
            "voice_video_face_sess",
            [
                ("fixture_chat.png", png, "image/png"),
                ("fixture_chat.mp3", mp3ish, "audio/mpeg"),
            ],
        )

    man = asyncio.run(run())
    items = man.get("items") or []
    assert len(items) == 2
    assert all(items[i].get("has_text") for i in range(2))

    sid = man["session_id"]
    ids = [items[0]["id"], items[1]["id"]]
    blob = ca.load_attachment_texts(sid, ids)
    assert "[Image:" in blob and "fixture_chat.png" in blob
    assert "[Media:" in blob and "fixture_chat.mp3" in blob


@pytest.mark.regression
def test_chat_mirror_ingests_media_into_from_chat_fts(isolated_rag_workspace):
    lr = isolated_rag_workspace["lr"]
    ca = isolated_rag_workspace["ca"]
    png = _minimal_png()

    async def run():
        return await ca.save_uploads("mirror_sess", [("fts_ping.png", png, "image/png")])

    man = asyncio.run(run())
    fc = lr.rag_from_chat_dir() / "mirror_sess"
    mirrored = list(fc.glob("*fts_ping.png"))
    assert mirrored and mirrored[0].is_file()
    r = lr.search_knowledge_base("Dimensions", limit=4)
    assert r.get("ok") and r.get("hits"), "FTS should match Pillow dimension line"
    assert any("Dimensions" in (h.get("snippet") or "") for h in r["hits"])


@pytest.mark.regression
def test_documents_dir_direct_media_ingest_and_search(isolated_rag_workspace):
    lr = isolated_rag_workspace["lr"]
    d = lr.rag_documents_dir()
    png = _minimal_png()
    p = d / "direct_upload_doc_folder.png"
    p.write_bytes(png)
    ing = lr.ingest_file(p)
    assert ing.get("ok") is True
    assert ing.get("chunks", 0) >= 1
    r = lr.search_knowledge_base("direct_upload_doc_folder PNG", limit=6)
    assert r.get("ok") and r.get("hits")


@pytest.mark.regression
def test_voice_rag_augment_finds_document_media_placeholder(isolated_rag_workspace, monkeypatch):
    lr = isolated_rag_workspace["lr"]
    monkeypatch.setenv("VOICE_LOCAL_RAG", "1")

    d = lr.rag_documents_dir()
    raw = b"\xff\xfb\x90\x00" + b"\x00" * 64
    target = d / "voice_query_unique_marker_mm.mp3"
    target.write_bytes(raw)
    ing = lr.ingest_file(target)
    assert ing.get("ok") is True
    out = lr.augment_voice_transcript_with_rag("What is in voice_query_unique_marker_mm.mp3")
    assert "voice_query_unique_marker_mm" in out.lower()
    assert "Media" in out or "Offline knowledge base" in out


@pytest.mark.regression
@pytest.mark.parametrize("mode", ["text_only", "text_plus_vision", "text_plus_vision_face"])
def test_text_chat_attachment_blob_survives_vision_augment_modes(
    isolated_rag_workspace, monkeypatch, mode
):
    """Attachments are merged into the user message; vision (video / face UI) appends after.

    Face and video use the same HTTP text-chat path for this step; only labels differ.
    """

    async def _run():
        ca = isolated_rag_workspace["ca"]
        png = _minimal_png()
        man = await ca.save_uploads(
            f"mode_{mode}",
            [("mode_attachment.png", png, "image/png")],
        )
        aid = man["items"][0]["id"]
        sid = man["session_id"]

        from pipecat_bots import vision_augment as va

        msgs = [{"role": "user", "content": "What do you see in the attachment?"}]
        msgs = ca.augment_last_user_with_attachments(msgs, sid, [aid])
        last = msgs[-1]["content"]
        assert "Dimensions" in last or "[Image:" in last

        vision_on = mode != "text_only"

        async def fake_augment_user_text_with_vision(
            text, session_id, vision_enable, utterance_source="text"
        ):
            if not vision_enable:
                return text
            return (text or "").rstrip() + f"\n\n[vision_augment_stub:{mode}]"

        monkeypatch.setattr(va, "augment_user_text_with_vision", fake_augment_user_text_with_vision)
        out = await va.augment_chat_messages_for_vision(msgs, "vision-sid-stub", vision_on)
        final = out[-1]["content"]
        assert "Dimensions" in final or "[Image:" in final
        if vision_on:
            assert f"[vision_augment_stub:{mode}]" in final
        else:
            assert "[vision_augment_stub:" not in final

    asyncio.run(_run())
