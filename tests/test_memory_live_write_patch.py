from pathlib import Path

import pytest

from hip_id_agent.memory import JsonFileStore


def test_json_store_falls_back_when_atomic_replace_is_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = JsonFileStore(tmp_path / "partners.json", default={"items": []})
    calls = {"count": 0}

    def deny_replace(src, dst):
        calls["count"] += 1
        raise PermissionError("simulated OneDrive rename lock")

    monkeypatch.setattr("hip_id_agent.memory.os.replace", deny_replace)
    payload = {"items": [{"id": "abc", "name": "AS2TEST"}]}
    store.write(payload)
    assert store.read() == payload
    assert calls["count"] >= 1
