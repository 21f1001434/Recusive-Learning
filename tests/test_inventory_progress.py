from __future__ import annotations

import json

from hip_id_agent.inventory import _InventoryProgress


def test_inventory_progress_writes_snapshot_events_and_heartbeat(tmp_path):
    progress = _InventoryProgress(tmp_path, print_interval_seconds=999)
    progress.start("partner_api_fanout", 10, detail="starting", counts={"account": 2, "partner": 0})
    progress.update(current=5, detail="half done", counts={"account": 2, "partner": 4}, force=True)
    progress.finish(status="partial_success", detail="done", counts={"account": 2, "partner": 4})

    snapshot_path = tmp_path / "inventory" / "progress.json"
    events_path = tmp_path / "inventory" / "progress_events.jsonl"
    heartbeat_path = tmp_path / "inventory" / "progress_heartbeat.txt"

    assert snapshot_path.exists()
    assert events_path.exists()
    assert heartbeat_path.exists()

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["status"] == "partial_success"
    assert snapshot["phase"] == "partner_api_fanout"
    assert snapshot["current"] == 10
    assert snapshot["total"] == 10
    assert snapshot["percent"] == 100.0
    assert snapshot["counts"]["partner"] == 4
    assert "stuck_rule" in snapshot

    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(events) >= 3
    assert heartbeat_path.read_text(encoding="utf-8").strip()
