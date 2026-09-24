import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from hip_id_agent.config import load_config
from hip_id_agent.memory import HipMemory
from hip_id_agent.models import RunContext
from hip_id_agent.partner_system_flow import PartnerSystemIDFlow
from hip_id_agent.report import ReportWriter


def _write_cfg(tmp_path: Path, backend: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(f"""
mcp:
  browser_backend: "{backend}"
reporting:
  runs_dir: "{tmp_path / 'runs'}"
  memory_dir: "{tmp_path / 'memory'}"
portal:
  headless: true
""", encoding="utf-8")
    return p


@pytest.mark.parametrize("backend", ["playwright", "auto", "mcp"])
def test_config_accepts_allowed_browser_backends(tmp_path: Path, backend: str):
    cfg = load_config(_write_cfg(tmp_path, backend))
    assert cfg.mcp.browser_backend == backend


def test_config_rejects_invalid_browser_backend(tmp_path: Path):
    with pytest.raises(ValidationError):
        load_config(_write_cfg(tmp_path, "selenium"))


def test_report_includes_auto_fallback_metadata(tmp_path: Path):
    ctx = RunContext("RUN1", "UHAL", "UHAL", "UHAL-SFTP", tmp_path, tmp_path / "screenshots")
    ctx.registry.update({
        "requested_browser_backend": "auto",
        "attempted_browser_backend": "mcp",
        "browser_backend_used": "playwright",
        "fallback_backend": "playwright",
        "fallback_reason": "MCP runtime not available in this local project; using Playwright fallback",
        "run_status": "partial_success",
    })
    summary = ReportWriter(ctx).build_summary()
    text = str(summary)
    assert "auto" in text
    assert "playwright" in text
    assert "fallback" in text.lower()


def test_mcp_backend_fails_fast_without_silent_playwright_fallback(tmp_path: Path):
    cfg = load_config(_write_cfg(tmp_path, "mcp"))
    mem = HipMemory(cfg.reporting.memory_dir)
    run_dir = tmp_path / "runs" / "MCPFAIL"
    ctx = RunContext("MCPFAIL", "UHAL", "UHAL", "UHAL-SFTP", run_dir, run_dir / "screenshots")
    summary = asyncio.run(PartnerSystemIDFlow(cfg, mem).run(ctx=ctx))
    assert summary["run_status"] == "failed"
    assert summary["registry"]["requested_browser_backend"] == "mcp"
    assert summary["registry"]["browser_backend_used"] == "mcp"
    assert "MCP backend was requested" in summary["registry"]["run_exception"]
    assert (run_dir / "failure_bundle" / "partial_knowledge_graph.json").exists()


def test_example_config_extraction_minimum_confidence_is_090():
    import yaml
    data = yaml.safe_load(Path("config.example.yaml").read_text(encoding="utf-8"))
    assert float(data["extraction"]["minimum_confidence"]) == 0.90
