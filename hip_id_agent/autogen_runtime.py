from __future__ import annotations

import importlib
import os
import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Tuple

AUTOGEN_REQUIRED_VERSION = "0.7.5"
AUTOGEN_PACKAGES = ("autogen-agentchat", "autogen-core", "autogen-ext")
AUTOGEN_IMPORT_MODULES = {
    "autogen-agentchat": "autogen_agentchat",
    "autogen-core": "autogen_core",
    "autogen-ext": "autogen_ext",
}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def autogen_strict_required() -> bool:
    return _truthy(os.getenv("HIP_REQUIRE_AUTOGEN_075")) or _truthy(os.getenv("AIA_REQUIRE_AUTOGEN_075"))


def _canonical_dist_name(value: str) -> str:
    """PEP-503 style normalization without depending on packaging metadata."""
    return re.sub(r"[-_.]+", "-", str(value or "").strip()).lower()


def _version_from_dist_info_dir(package: str) -> Tuple[str, str]:
    """Recover a distribution version from its *.dist-info directory name.

    This intentionally exists as a fallback for corporate/OneDrive venvs where the
    METADATA file can be temporarily incomplete or unreadable while the installed
    package and its dist-info directory are otherwise present.
    """
    wanted = _canonical_dist_name(package)
    for raw_entry in sys.path:
        if not raw_entry:
            continue
        entry = Path(raw_entry)
        if not entry.is_dir():
            continue
        try:
            candidates = entry.glob("*.dist-info")
            for candidate in candidates:
                stem = candidate.name[: -len(".dist-info")]
                # Wheel dist-info names conventionally use <normalized-name>-<version>.
                if "-" not in stem:
                    continue
                dist_name, version = stem.rsplit("-", 1)
                if _canonical_dist_name(dist_name) == wanted and version:
                    return version, str(candidate)
        except (OSError, PermissionError):
            continue
    return "", ""


def _version_from_import(package: str) -> Tuple[str, str]:
    module_name = AUTOGEN_IMPORT_MODULES.get(package, package.replace("-", "_"))
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return "", ""
    value = str(getattr(module, "__version__", "") or "").strip()
    if value:
        return value, f"{module_name}.__version__"
    return "", ""


def _detect_distribution_version(package: str) -> Tuple[str, str, str]:
    """Return (version, source, metadata_error) using resilient layered detection."""
    metadata_error = ""
    try:
        value = str(metadata.version(package) or "").strip()
        if value:
            return value, "importlib.metadata", ""
    except Exception as exc:  # metadata corruption can raise more than PackageNotFoundError
        metadata_error = f"{type(exc).__name__}: {exc}"

    value, source = _version_from_dist_info_dir(package)
    if value:
        return value, "dist-info-directory", metadata_error

    value, source = _version_from_import(package)
    if value:
        return value, source, metadata_error

    return "", "", metadata_error


def autogen_runtime_status(*, verify_imports: bool = False) -> Dict[str, Any]:
    versions: Dict[str, str] = {}
    version_sources: Dict[str, str] = {}
    metadata_errors: Dict[str, str] = {}
    missing: list[str] = []
    wrong: Dict[str, str] = {}

    for package in AUTOGEN_PACKAGES:
        version, source, metadata_error = _detect_distribution_version(package)
        versions[package] = version
        version_sources[package] = source
        if metadata_error:
            metadata_errors[package] = metadata_error
        if not version:
            missing.append(package)
        elif version != AUTOGEN_REQUIRED_VERSION:
            wrong[package] = version

    imports_ok = None
    import_error = ""
    imported_components: list[str] = []
    if verify_imports and not missing and not wrong:
        try:
            from autogen_agentchat.agents import AssistantAgent  # noqa: F401
            imported_components.append("autogen_agentchat.agents.AssistantAgent")
            import autogen_core  # noqa: F401
            imported_components.append("autogen_core")
            from autogen_ext.models.openai import OpenAIChatCompletionClient  # noqa: F401
            imported_components.append("autogen_ext.models.openai.OpenAIChatCompletionClient")
            imports_ok = True
        except Exception as exc:  # environment/dependency specific
            imports_ok = False
            import_error = f"{type(exc).__name__}: {exc}"

    pass_flag = not missing and not wrong and imports_ok is not False
    if pass_flag:
        reason = "AutoGen 0.7.5 detected and required runtime imports are ready."
    elif missing:
        reason = "AutoGen distribution version could not be proven for: " + ", ".join(missing)
    elif wrong:
        reason = "AutoGen version mismatch: " + ", ".join(f"{k}={v}" for k, v in wrong.items())
    else:
        reason = "AutoGen 0.7.5 is installed, but a required runtime import failed: " + import_error

    return {
        "required_version": AUTOGEN_REQUIRED_VERSION,
        "packages": versions,
        "version_sources": version_sources,
        "metadata_errors": metadata_errors,
        "missing": missing,
        "wrong_version": wrong,
        "imports_checked": bool(verify_imports),
        "imports_ok": imports_ok,
        "imported_components": imported_components,
        "import_error": import_error,
        "pass": pass_flag,
        "reason": reason,
        "python_executable": sys.executable,
        "install_command": (
            'python -m pip install --upgrade '
            '"autogen-agentchat==0.7.5" "autogen-core==0.7.5" "autogen-ext[openai]==0.7.5"'
        ),
    }


def assert_autogen_075(*, verify_imports: bool = True) -> Dict[str, Any]:
    status = autogen_runtime_status(verify_imports=verify_imports)
    if not status["pass"]:
        details = []
        if status["missing"]:
            details.append("missing=" + ",".join(status["missing"]))
        if status["wrong_version"]:
            details.append(
                "wrong_version="
                + ",".join(f"{name}:{version}" for name, version in status["wrong_version"].items())
            )
        if status.get("imports_ok") is False:
            details.append("import_error=" + str(status.get("import_error") or "unknown"))
        if status.get("metadata_errors"):
            details.append(
                "metadata_warning="
                + ",".join(f"{name}:{message}" for name, message in status["metadata_errors"].items())
            )
        raise RuntimeError(
            "Microsoft AutoGen AgentChat 0.7.5 runtime preflight failed ("
            + "; ".join(details)
            + "). "
            + str(status.get("reason") or "")
            + " Install/repair only if needed with: "
            + status["install_command"]
        )
    return status
