"""identity_is_enrolled() rules — registry-backed enrollment detection."""

from __future__ import annotations

import pytest

from pipecat_bots.face_recog import person_registry as pr
from pipecat_bots.face_recog.config import set_facerecog_runtime


@pytest.fixture
def iso_reg(monkeypatch, tmp_path):
    monkeypatch.setenv("FACERECOG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FACERECOG_ENABLED", "1")
    pr._conn = None
    set_facerecog_runtime(True)
    yield
    pr._conn = None


@pytest.mark.regression
@pytest.mark.face
def test_enrolled_requires_non_placeholder_name_and_not_id_fallback(iso_reg):
    pr.upsert_person("p_real", "Alice", embedding=None)
    pr.upsert_person("p_unknown", "Unknown", embedding=None)
    pr.upsert_person("p_empty", "", embedding=None)
    assert pr.identity_is_enrolled("p_real") is True
    assert pr.identity_is_enrolled("p_unknown") is False
    assert pr.identity_is_enrolled("p_empty") is False
    assert pr.identity_is_enrolled("missing") is False


@pytest.mark.regression
@pytest.mark.face
def test_display_name_equals_person_id_not_enrolled(iso_reg):
    pr.upsert_person("p_self", "p_self", embedding=None)
    assert pr.identity_is_enrolled("p_self") is False
