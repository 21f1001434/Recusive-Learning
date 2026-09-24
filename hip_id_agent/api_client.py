from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests

from .config import APIConfig
from .security import mask_sensitive_data

PARTNER_KEYS = {"partnerid", "partner_id", "partneridentifier", "partneridentifierid", "partneruuid"}
SYSTEM_KEYS = {"systemid", "system_id", "domainid", "domain_id", "systemidentifier", "systemuuid"}
ACCOUNT_KEYS = {"accountid", "account_id", "xaccountid"}


def _norm_key(k: str) -> str:
    return str(k).replace("-", "").replace("_", "").replace(" ", "").lower()


class HipAPIClient:
    """Optional API execution/enrichment helper.

    The main task is enrichment: inject partner/system IDs discovered from the portal into
    input.json so downstream HIP APIs can run. Real API execution stays disabled/dry-run unless
    explicitly enabled in config and --run-api is passed.
    """

    def __init__(self, config: APIConfig):
        self.config = config

    def _get_token(self) -> Optional[str]:
        token = os.getenv(self.config.token_env_var)
        if token:
            return token
        if self.config.token_command:
            try:
                proc = subprocess.run(self.config.token_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60, check=False)
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.strip()
            except Exception:
                return None
        return None

    def enrich_payload(self, data: Any, *, partner_id: str | None, system_id: str | None, account_id: str | None = None) -> Any:
        def is_empty_or_placeholder(value: Any) -> bool:
            if value is None:
                return True
            if isinstance(value, str):
                v = value.strip()
                return v == "" or v.upper() in {"TBD", "NA", "N/A", "NULL"} or v in {
                    "${partner_id}", "{{partner_id}}", "${system_id}", "{{system_id}}", "${account_id}", "{{account_id}}"
                }
            return False

        def walk(node: Any) -> Any:
            if isinstance(node, dict):
                out = {}
                for k, v in node.items():
                    nk = _norm_key(k)
                    if nk in PARTNER_KEYS and partner_id and is_empty_or_placeholder(v):
                        out[k] = partner_id
                    elif nk in SYSTEM_KEYS and system_id and is_empty_or_placeholder(v):
                        out[k] = system_id
                    elif nk in ACCOUNT_KEYS and account_id and is_empty_or_placeholder(v):
                        out[k] = account_id
                    else:
                        out[k] = walk(v)
                return out
            if isinstance(node, list):
                return [walk(x) for x in node]
            if isinstance(node, str):
                if partner_id:
                    node = node.replace("${partner_id}", partner_id).replace("{{partner_id}}", partner_id)
                if system_id:
                    node = node.replace("${system_id}", system_id).replace("{{system_id}}", system_id)
                if account_id:
                    node = node.replace("${account_id}", account_id).replace("{{account_id}}", account_id)
            return node

        return walk(data)

    def save_enriched_input(self, input_path: str | Path, output_path: str | Path, *, partner_id: str | None, system_id: str | None) -> Dict[str, Any]:
        """Save two outputs:

        1. enriched_input.json: real execution file, not masked, usable by API execution.
        2. enriched_input.redacted.json: safe reporting file with secrets masked.

        Reports and Knowledge Graph should reference the redacted path; API execution should use the
        in-memory/real enriched payload.
        """
        inp = Path(input_path)
        data = json.loads(inp.read_text(encoding="utf-8"))
        enriched = self.enrich_payload(data, partner_id=partner_id, system_id=system_id, account_id=self.config.account_id or None)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        redacted_out = out.with_name(out.stem + ".redacted" + out.suffix)
        out.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
        redacted_out.write_text(json.dumps(mask_sensitive_data(enriched), indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "input": str(inp),
            "output": str(out),
            "redacted_output": str(redacted_out),
            "dry_run": self.config.dry_run,
            "partner_id_injected": bool(partner_id),
            "system_id_injected": bool(system_id),
            "execution_file_unmasked": True,
            "report_file_redacted": True,
        }

    def run_configured_requests(self, enriched_payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.config.enabled:
            return {"status": "skipped", "reason": "API disabled in config"}
        token = self._get_token()
        if not token:
            return {"status": "failed", "reason": f"Missing token env/command for {self.config.token_env_var}"}
        if not self.config.base_url:
            return {"status": "failed", "reason": "api.base_url not configured"}
        results = []
        for req in self.config.requests:
            try:
                body = self._get_by_path(enriched_payload, req.body_path) if req.body_path else enriched_payload
                url = urljoin(self.config.base_url.rstrip("/") + "/", req.path.lstrip("/"))
                if self.config.dry_run:
                    results.append({"name": req.name, "method": req.method, "url": url, "status": "dry_run", "body_preview": mask_sensitive_data(body)})
                    continue
                resp = requests.request(
                    req.method.upper(),
                    url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "x-requester-id": self.config.requester_id,
                        "x-account-id": self.config.account_id,
                    },
                    timeout=90,
                )
                try:
                    response_body = resp.json()
                except Exception:
                    response_body = resp.text[:3000]
                results.append({"name": req.name, "method": req.method, "url": url, "status_code": resp.status_code, "response": mask_sensitive_data(response_body)})
            except Exception as exc:
                results.append({"name": req.name, "status": "failed", "error": str(exc)})
        return {"status": "done", "results": results}

    def _get_by_path(self, data: Any, path: str) -> Any:
        cur = data
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur[part]
            elif isinstance(cur, list):
                cur = cur[int(part)]
            else:
                raise KeyError(path)
        return cur
