from __future__ import annotations

from pathlib import Path

from hip_id_agent import autogen_runtime

ROOT = Path(__file__).resolve().parents[1]


def test_requirements_pin_exact_autogen_075_stack():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "autogen-agentchat==0.7.5" in text
    assert "autogen-core==0.7.5" in text
    assert "autogen-ext[openai]==0.7.5" in text
    assert "autogen-agentchat>=0.4.0" not in text


def test_runtime_status_accepts_exact_075(monkeypatch):
    def fake_version(name: str) -> str:
        assert name in autogen_runtime.AUTOGEN_PACKAGES
        return "0.7.5"

    monkeypatch.setattr(autogen_runtime.metadata, "version", fake_version)
    status = autogen_runtime.autogen_runtime_status(verify_imports=False)
    assert status["pass"] is True
    assert status["required_version"] == "0.7.5"
    assert not status["missing"]
    assert not status["wrong_version"]


def test_runtime_status_rejects_wrong_version(monkeypatch):
    versions = {
        "autogen-agentchat": "0.7.5",
        "autogen-core": "0.7.4",
        "autogen-ext": "0.7.5",
    }
    monkeypatch.setattr(autogen_runtime.metadata, "version", lambda name: versions[name])
    status = autogen_runtime.autogen_runtime_status(verify_imports=False)
    assert status["pass"] is False
    assert status["wrong_version"] == {"autogen-core": "0.7.4"}


def test_autonomous_cli_requires_autogen_075_and_enables_agentchat():
    source = (ROOT / "hip_id_agent" / "cli.py").read_text(encoding="utf-8")
    block = source[source.index("if autonomous_mission:"):source.index("if all_phases_until_complete:")]
    assert 'os.environ["AIA_USE_AUTOGEN"] = "true"' in block
    assert 'os.environ["HIP_USE_LLM_FORM_PLANNER"] = "true"' in block
    assert 'os.environ["HIP_REQUIRE_AUTOGEN_075"] = "true"' in block
    assert 'autogen_runtime_status(verify_imports=True)' in source
    assert 'run_dir / "autogen_preflight.json"' in source


def test_autogen_is_used_by_recovery_advisor_not_raw_rest():
    source = (ROOT / "hip_id_agent" / "runtime_self_heal.py").read_text(encoding="utf-8")
    block = source[source.index("async def _aia_advice"):source.index("async def _execute_action")]
    assert "client.json_decision" in block
    assert "client.chat_rest" not in block


def test_strict_autogen_disables_rest_fallback():
    source = (ROOT / "hip_id_agent" / "aia_client.py").read_text(encoding="utf-8")
    block = source[source.index("def autogen_reply"):source.index("def json_decision")]
    assert "strict_autogen = autogen_strict_required()" in block
    assert "assert_autogen_075(verify_imports=True)" in block
    assert "if strict_autogen:" in block
    assert "raise" in block


def test_runtime_status_recovers_exact_version_from_dist_info_when_metadata_is_broken(monkeypatch):
    def broken_version(name: str) -> str:
        raise KeyError("Name")

    monkeypatch.setattr(autogen_runtime.metadata, "version", broken_version)
    monkeypatch.setattr(
        autogen_runtime,
        "_version_from_dist_info_dir",
        lambda package: ("0.7.5", f"/venv/site-packages/{package.replace('-', '_')}-0.7.5.dist-info"),
    )
    status = autogen_runtime.autogen_runtime_status(verify_imports=False)
    assert status["pass"] is True
    assert status["packages"] == {name: "0.7.5" for name in autogen_runtime.AUTOGEN_PACKAGES}
    assert set(status["metadata_errors"]) == set(autogen_runtime.AUTOGEN_PACKAGES)
    assert all(source == "dist-info-directory" for source in status["version_sources"].values())


def test_runtime_status_reports_import_failure_separately_from_installation(monkeypatch):
    monkeypatch.setattr(autogen_runtime.metadata, "version", lambda name: "0.7.5")
    real_import = autogen_runtime.importlib.import_module

    def fake_import(name: str, *args, **kwargs):
        if name == "autogen_agentchat.agents":
            raise RuntimeError("dependency import failed")
        return real_import(name, *args, **kwargs)

    # This test only checks the diagnostic contract without relying on local AutoGen availability.
    monkeypatch.setattr(autogen_runtime, "_version_from_import", lambda package: ("", ""))
    status = autogen_runtime.autogen_runtime_status(verify_imports=False)
    assert status["pass"] is True
    assert "detected" in status["reason"].lower()


def test_live_windows_venv_compatibility_pins_are_shipped():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # browser-use 0.13.8 requires websockets==15.0.1; 13.1 made pip resolution impossible.
    assert "websockets==15.0.1" in requirements
    assert "packaging>=23.2,<26" in requirements
    assert '"websockets==15.0.1"' in pyproject
    assert '"packaging>=23.2,<26"' in pyproject
