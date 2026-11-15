"""Item 24: grocery-token secret storage."""
import json
from pathlib import Path

from app import grocery_secret
from app.settings import settings


def test_unpaired_by_default(tmp_stores, monkeypatch):
    monkeypatch.setattr(settings, "grocery_secret_path", str(tmp_stores / "g.json"))
    grocery_secret._reset_cache_for_tests()
    assert grocery_secret.get_token() is None
    assert grocery_secret.is_paired() is False


def test_set_persists_and_rotates(tmp_stores, monkeypatch):
    p = tmp_stores / "g.json"
    monkeypatch.setattr(settings, "grocery_secret_path", str(p))
    grocery_secret._reset_cache_for_tests()
    grocery_secret.set_token("x" * 48)
    assert grocery_secret.get_token() == "x" * 48
    assert grocery_secret.is_paired() is True
    # Rotate.
    grocery_secret.set_token("y" * 48)
    assert grocery_secret.get_token() == "y" * 48
    # On-disk payload reflects the latest.
    data = json.loads(Path(p).read_text())
    assert data["token"] == "y" * 48


def test_clear_removes_file_and_cache(tmp_stores, monkeypatch):
    p = tmp_stores / "g.json"
    monkeypatch.setattr(settings, "grocery_secret_path", str(p))
    grocery_secret._reset_cache_for_tests()
    grocery_secret.set_token("abc" * 20)
    assert grocery_secret.clear() is True
    assert not Path(p).exists()
    assert grocery_secret.get_token() is None
    # Second clear is a no-op.
    assert grocery_secret.clear() is False


def test_set_rejects_empty(tmp_stores, monkeypatch):
    monkeypatch.setattr(settings, "grocery_secret_path", str(tmp_stores / "g.json"))
    grocery_secret._reset_cache_for_tests()
    import pytest
    with pytest.raises(ValueError):
        grocery_secret.set_token("")


def test_corrupt_file_treated_as_unpaired(tmp_stores, monkeypatch):
    p = tmp_stores / "g.json"
    monkeypatch.setattr(settings, "grocery_secret_path", str(p))
    p.write_text("not json at all", encoding="utf-8")
    grocery_secret._reset_cache_for_tests()
    assert grocery_secret.get_token() is None
