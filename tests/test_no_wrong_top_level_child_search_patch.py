from pathlib import Path
import inspect
import yaml

from hip_id_agent.config import AppConfig, load_config
from hip_id_agent.partner_system_flow import PartnerSystemIDFlow


def test_exploration_defaults_do_not_search_child_in_top_level_grid():
    cfg = AppConfig()
    assert cfg.exploration.prefer_nested_discovery is True
    assert cfg.exploration.search_first is False
    assert cfg.exploration.allow_direct_child_search_fallback is False


def test_example_and_runtime_config_disable_direct_child_search():
    for name in ["config.yaml", "config.example.yaml"]:
        raw = yaml.safe_load(Path(name).read_text(encoding="utf-8"))
        assert raw["exploration"]["prefer_nested_discovery"] is True
        assert raw["exploration"]["search_first"] is False
        assert raw["exploration"]["allow_direct_child_search_fallback"] is False


def test_extract_one_skips_direct_child_search_when_nested_preferred():
    source = inspect.getsource(PartnerSystemIDFlow._extract_one)
    assert "allow_direct_child_search_fallback" in source
    assert "direct child top-level search is disabled" in source
    assert "if not extracted and allow_direct_child_search" in source


def test_exploration_report_does_not_pass_child_query_to_broad_explore_when_nested_enabled():
    source = inspect.getsource(PartnerSystemIDFlow.run)
    assert 'initial_query=None if self.config.exploration.prefer_nested_discovery else (ctx.partner_query or None)' in source
    assert 'initial_query=None if self.config.exploration.prefer_nested_discovery else (ctx.system_query or None)' in source
