from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import utc_now
from .safe_io import safe_write_json
from .security import mask_sensitive_data, mask_sensitive_string


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
VALIDATION_TOKENS = ("validate", "validation", "verify", "check", "exists", "duplicate", "availability")
LIST_TOKENS = ("list", "search", "query", "options", "lookup", "dropdown", "catalog", "reference")
CREATE_TOKENS = ("create", "add", "save")
UPDATE_TOKENS = ("update", "edit", "patch")
DELETE_TOKENS = ("delete", "remove")

PHASE_OBJECT_KEYS = {
    "data_map": "data_map",
    "source_document_type": "source_document_type",
    "target_document_type": "target_document_type",
    "rule": "rule",
    "source_transport_profile": "source_transport_profile",
    "target_transport_profile": "target_transport_profile",
    "biz_flow": "biz_flow",
}


def _hash(value: Any, length: int = 20) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _event_dict(value: Any) -> Dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    return dict(value) if isinstance(value, Mapping) else {}


def _try_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text or text[:1] not in {"{", "["}:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _strip_query(url: str) -> str:
    return str(url or "").split("?", 1)[0]


def _redact_url_query(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
        query = urlencode([(key, "<redacted>") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, ""))
    except Exception:
        return _strip_query(mask_sensitive_string(str(url or "")))


def endpoint_template(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return _strip_query(url)
    parts: List[str] = []
    for raw in (parsed.path or "/").split("/"):
        part = raw.strip()
        if not part:
            continue
        if re.fullmatch(r"\d+", part):
            part = "{id}"
        elif re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27,}", part, re.I):
            part = "{uuid}"
        elif re.fullmatch(r"[0-9a-f]{16,}", part, re.I):
            part = "{token}"
        parts.append(part)
    host = (parsed.hostname or "").lower()
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host}/" + "/".join(parts)


def type_shape(value: Any, depth: int = 0) -> Any:
    if depth >= 6:
        return type(value).__name__
    if isinstance(value, Mapping):
        return {
            str(k): type_shape(v, depth + 1)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))[:300]
        }
    if isinstance(value, list):
        if not value:
            return []
        shapes: List[Any] = []
        seen: set[str] = set()
        for item in value[:5]:
            shape = type_shape(item, depth + 1)
            token = json.dumps(shape, sort_keys=True, default=str)
            if token not in seen:
                seen.add(token)
                shapes.append(shape)
        return shapes[:3]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def flatten_json(value: Any, prefix: str = "$") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix != "$" else f"$.{key}"
            out.update(flatten_json(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            out.update(flatten_json(child, f"{prefix}[{index}]"))
    else:
        out[prefix] = value
    return out


def _get_json_path(value: Any, path: str) -> Any:
    if not path or not path.startswith("$"):
        return None
    current = value
    for key, index in re.findall(r"\.([^.\[]+)|\[(\d+)\]", path[1:]):
        if key:
            if not isinstance(current, Mapping) or key not in current:
                return None
            current = current[key]
        else:
            if not isinstance(current, list) or int(index) >= len(current):
                return None
            current = current[int(index)]
    return current


def phase_payload(input_payload: Mapping[str, Any], phase: str) -> Any:
    objects = input_payload.get("objects") if isinstance(input_payload, Mapping) else None
    key = PHASE_OBJECT_KEYS.get(str(phase or ""))
    if isinstance(objects, Mapping) and key in objects:
        return objects.get(key)
    return {}


def classify_endpoint(method: str, url: str, request_body: Any = None) -> str:
    method = str(method or "GET").upper()
    text = _norm(url)
    if method in SAFE_METHODS:
        return "reference_or_read" if any(token in text for token in LIST_TOKENS) else "read"
    if any(token in text for token in VALIDATION_TOKENS):
        return "validation"
    if method == "DELETE" or any(token in text for token in DELETE_TOKENS):
        return "delete"
    if method in {"PUT", "PATCH"} or any(token in text for token in UPDATE_TOKENS):
        return "update"
    if method == "POST" and any(token in text for token in CREATE_TOKENS):
        return "create"
    if method == "POST":
        body = request_body if isinstance(request_body, Mapping) else {}
        body_keys = {_norm(key) for key in body}
        if body_keys.intersection({"page", "pagesize", "filter", "query", "search", "sort"}):
            return "search"
        return "post_unknown"
    return "unknown"


def build_api_contracts(events: Iterable[Any], *, max_contracts: int = 500) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for raw in events:
        event = _event_dict(raw)
        url = str(event.get("url") or "")
        if not url.lower().startswith(("http://", "https://")):
            continue
        method = str(event.get("method") or "GET").upper()
        request = event.get("request_body_redacted")
        if request is None:
            request = _try_json(event.get("request_post_data"))
        response = event.get("response_body_redacted")
        if response is None:
            response = _try_json(event.get("response_body_text_redacted") or event.get("response_body"))
        template = endpoint_template(url)
        kind = classify_endpoint(method, template, request)
        key = f"{method} {template}"
        row = grouped.setdefault(key, {
            "contract_id": _hash(key),
            "method": method,
            "endpoint_template": template,
            "endpoint_kind": kind,
            "observed_urls": [],
            "statuses": [],
            "resource_types": [],
            "query_keys": [],
            "request_shapes": [],
            "response_shapes": [],
            "request_examples_redacted": [],
            "response_examples_redacted": [],
            "stages": [],
            "sources": [],
            "initiators": [],
        })
        row["observed_urls"].append(_strip_query(url))
        if event.get("status") is not None:
            row["statuses"].append(event.get("status"))
        row["resource_types"].append(event.get("resource_type"))
        row["stages"].append(event.get("stage"))
        row["sources"].append(event.get("source"))
        if event.get("initiator"):
            row["initiators"].append(mask_sensitive_data(event.get("initiator")))
        try:
            row["query_keys"].extend(k for k, _ in parse_qsl(urlparse(url).query, keep_blank_values=True))
        except Exception:
            pass
        if request not in (None, ""):
            row["request_shapes"].append(type_shape(request))
            row["request_examples_redacted"].append(mask_sensitive_data(request))
        if response not in (None, ""):
            row["response_shapes"].append(type_shape(response))
            row["response_examples_redacted"].append(mask_sensitive_data(response))
        if len(grouped) >= max_contracts:
            break

    for row in grouped.values():
        for name, limit in (
            ("observed_urls", 10), ("statuses", 20), ("resource_types", 20),
            ("query_keys", 80), ("request_shapes", 10), ("response_shapes", 10),
            ("request_examples_redacted", 3), ("response_examples_redacted", 2),
            ("stages", 30), ("sources", 20), ("initiators", 3),
        ):
            unique: List[Any] = []
            seen: set[str] = set()
            for value in row.get(name) or []:
                token = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
                if token not in seen:
                    seen.add(token)
                    unique.append(value)
            row[name] = unique[:limit]
        row["mutation_capable"] = row["method"] in MUTATING_METHODS and row["endpoint_kind"] not in {"validation", "search"}
        row["safe_to_replay_automatically"] = row["method"] in SAFE_METHODS or row["endpoint_kind"] == "validation"
    return sorted(grouped.values(), key=lambda item: (item["endpoint_template"], item["method"]))



def _redacted_headers(headers: Any) -> Dict[str, Any]:
    """Return persisted-safe headers while preserving useful API metadata."""
    if not isinstance(headers, Mapping):
        return {}
    blocked = {"authorization", "cookie", "set-cookie", "proxy-authorization"}
    out: Dict[str, Any] = {}
    for key, value in headers.items():
        name = str(key or "")
        if name.lower() in blocked:
            out[name] = "***MASKED***"
        else:
            out[name] = mask_sensitive_data(value)
    return out


def build_api_transactions(events: Iterable[Any], *, max_transactions: int = 2000) -> List[Dict[str, Any]]:
    """Build request/response pairs for every observed form API transaction.

    Contracts intentionally aggregate shapes. This transaction ledger preserves the
    per-request stage, redacted request payload, response status and redacted response
    payload so the Streamlit UI and exported evidence can show exactly what happened
    while opening and filling each form. Duplicate CDP/Playwright observations are
    merged, preferring the record with the richest response evidence.
    """
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in events:
        event = _event_dict(raw)
        url = str(event.get("url") or "")
        if not url.lower().startswith(("http://", "https://")):
            continue
        method = str(event.get("method") or "GET").upper()
        request_payload = event.get("request_body_redacted")
        if request_payload is None:
            request_payload = _try_json(event.get("request_post_data"))
        if request_payload is None and event.get("request_post_data") not in (None, ""):
            request_payload = mask_sensitive_string(str(event.get("request_post_data")))

        response_payload = event.get("response_body_redacted")
        response_text = event.get("response_body_text_redacted")
        if response_payload is None:
            response_payload = _try_json(response_text or event.get("response_body"))
        if response_payload is None and (response_text or event.get("response_body")) not in (None, ""):
            response_payload = mask_sensitive_string(str(response_text or event.get("response_body")))

        request_id = str(event.get("request_id") or "")
        fingerprint = _hash({
            "method": method,
            "url": _strip_query(url),
            "stage": event.get("stage"),
            "request": mask_sensitive_data(request_payload),
            "status": event.get("status"),
        }, 24)
        key = request_id or fingerprint
        row = {
            "transaction_id": request_id or fingerprint,
            "timestamp": event.get("timestamp"),
            "stage": event.get("stage") or "unknown",
            "source": event.get("source") or "browser_network",
            "resource_type": event.get("resource_type") or "unknown",
            "method": method,
            "url_redacted": _redact_url_query(url),
            "query_keys": sorted({key for key, _ in parse_qsl(urlparse(url).query, keep_blank_values=True)}),
            "endpoint_template": endpoint_template(url),
            "endpoint_kind": classify_endpoint(method, url, request_payload),
            "request": {
                "headers_redacted": _redacted_headers(event.get("request_headers")),
                "payload_redacted": mask_sensitive_data(request_payload),
                "payload_captured": request_payload not in (None, ""),
                "shape": type_shape(request_payload) if request_payload not in (None, "") else None,
            },
            "response": {
                "status": event.get("status"),
                "ok": (200 <= int(event.get("status")) < 400) if str(event.get("status") or "").isdigit() else None,
                "headers_redacted": _redacted_headers(event.get("response_headers")),
                "mime_type": event.get("mime_type") or event.get("content_type"),
                "payload_redacted": mask_sensitive_data(response_payload),
                "payload_captured": response_payload not in (None, ""),
                "shape": type_shape(response_payload) if response_payload not in (None, "") else None,
                "capture_status": event.get("response_body_capture_status") or ("captured" if response_payload not in (None, "") else "not_captured"),
                "truncated": bool(event.get("response_body_truncated")),
            },
            "error": mask_sensitive_string(str(event.get("error") or "")),
            "initiator_redacted": mask_sensitive_data(event.get("initiator")),
            "mutation_capable": method in MUTATING_METHODS and classify_endpoint(method, url, request_payload) not in {"validation", "search"},
        }
        existing = rows.get(key)
        if existing is None:
            rows[key] = row
        else:
            existing_score = int(bool((existing.get("response") or {}).get("payload_captured"))) + int(bool((existing.get("request") or {}).get("payload_captured")))
            new_score = int(bool(row["response"]["payload_captured"])) + int(bool(row["request"]["payload_captured"]))
            if new_score > existing_score:
                rows[key] = row
            else:
                if existing.get("response", {}).get("status") is None and row["response"].get("status") is not None:
                    existing["response"]["status"] = row["response"].get("status")
                    existing["response"]["ok"] = row["response"].get("ok")
                if not existing.get("source") and row.get("source"):
                    existing["source"] = row.get("source")
        if len(rows) >= max_transactions:
            break
    return sorted(rows.values(), key=lambda item: (str(item.get("timestamp") or ""), str(item.get("transaction_id") or "")))


def build_payload_response_index(transactions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    request_payloads = sum(1 for row in transactions if bool((row.get("request") or {}).get("payload_captured")))
    response_payloads = sum(1 for row in transactions if bool((row.get("response") or {}).get("payload_captured")))
    responses = sum(1 for row in transactions if (row.get("response") or {}).get("status") is not None)
    mutations = sum(1 for row in transactions if bool(row.get("mutation_capable")))
    by_stage: Dict[str, Dict[str, int]] = {}
    for row in transactions:
        stage = str(row.get("stage") or "unknown")
        bucket = by_stage.setdefault(stage, {"transactions": 0, "request_payloads": 0, "responses": 0, "response_payloads": 0})
        bucket["transactions"] += 1
        bucket["request_payloads"] += int(bool((row.get("request") or {}).get("payload_captured")))
        bucket["responses"] += int((row.get("response") or {}).get("status") is not None)
        bucket["response_payloads"] += int(bool((row.get("response") or {}).get("payload_captured")))
    return {
        "schema_version": "hip.form-api-payload-response-index.v1",
        "transaction_count": len(transactions),
        "request_payload_count": request_payloads,
        "response_status_count": responses,
        "response_payload_count": response_payloads,
        "mutation_capable_transaction_count": mutations,
        "request_payload_coverage": round(request_payloads / len(transactions), 4) if transactions else 0.0,
        "response_status_coverage": round(responses / len(transactions), 4) if transactions else 0.0,
        "response_payload_coverage": round(response_payloads / len(transactions), 4) if transactions else 0.0,
        "by_stage": by_stage,
        "values_redacted": True,
        "authorization_persisted": False,
        "cookies_persisted": False,
    }



def build_action_api_causal_trace(
    actions: Sequence[Any],
    transactions: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Correlate each browser action with the API requests it actually triggered.

    The BrowserSession records the exact network-event slice present between
    action start and action completion.  This gives the recovery agent a causal
    UI -> API trace instead of merely a phase-wide bag of requests.
    """
    tx_by_id = {str(row.get("transaction_id") or ""): row for row in transactions if row.get("transaction_id")}
    rows: List[Dict[str, Any]] = []
    linked_tx: set[str] = set()
    for raw in actions:
        action = _event_dict(raw)
        ids = [str(x) for x in action.get("network_events_triggered") or [] if str(x)]
        related = []
        for request_id in ids:
            tx = tx_by_id.get(request_id)
            if not tx:
                continue
            linked_tx.add(request_id)
            related.append({
                "transaction_id": request_id,
                "method": tx.get("method"),
                "endpoint_template": tx.get("endpoint_template"),
                "endpoint_kind": tx.get("endpoint_kind"),
                "status": (tx.get("response") or {}).get("status"),
                "request_payload_captured": bool((tx.get("request") or {}).get("payload_captured")),
                "response_payload_captured": bool((tx.get("response") or {}).get("payload_captured")),
            })
        rows.append({
            "action_id": action.get("action_id"),
            "type": action.get("type"),
            "target": mask_sensitive_string(str(action.get("target") or "")),
            "stage": action.get("stage"),
            "success": bool(action.get("success", True)),
            "network_event_start_index": action.get("network_event_start_index"),
            "network_event_end_index": action.get("network_event_end_index"),
            "triggered_api_count": len(related),
            "triggered_transactions": related,
            "value_persisted": False,
        })
    orphan = [
        {
            "transaction_id": row.get("transaction_id"),
            "method": row.get("method"),
            "endpoint_template": row.get("endpoint_template"),
            "stage": row.get("stage"),
            "status": (row.get("response") or {}).get("status"),
        }
        for row in transactions
        if str(row.get("transaction_id") or "") not in linked_tx
    ]
    return mask_sensitive_data({
        "schema_version": "hip.ui-api-causal-trace.v1",
        "action_count": len(rows),
        "transaction_count": len(transactions),
        "linked_transaction_count": len(linked_tx),
        "linked_transaction_coverage": round(len(linked_tx) / len(transactions), 4) if transactions else 1.0,
        "actions": rows,
        "unlinked_transactions": orphan[:500],
        "values_stored": False,
    })


def build_redacted_har(transactions: Sequence[Mapping[str, Any]], *, title: str) -> Dict[str, Any]:
    """Return a compact HAR-like, fully redacted network archive."""
    entries: List[Dict[str, Any]] = []
    for row in transactions:
        request = row.get("request") if isinstance(row.get("request"), Mapping) else {}
        response = row.get("response") if isinstance(row.get("response"), Mapping) else {}
        entries.append({
            "startedDateTime": row.get("timestamp"),
            "request": {
                "method": row.get("method"),
                "url": row.get("url_redacted"),
                "headers": request.get("headers_redacted") or {},
                "postData": request.get("payload_redacted"),
            },
            "response": {
                "status": response.get("status"),
                "headers": response.get("headers_redacted") or {},
                "content": {
                    "mimeType": response.get("mime_type"),
                    "text": response.get("payload_redacted"),
                    "captureStatus": response.get("capture_status"),
                    "truncated": bool(response.get("truncated")),
                },
            },
            "_hip": {
                "transactionId": row.get("transaction_id"),
                "stage": row.get("stage"),
                "endpointKind": row.get("endpoint_kind"),
                "resourceType": row.get("resource_type"),
                "mutationCapable": bool(row.get("mutation_capable")),
            },
        })
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "HIP AgentQ", "version": "1"},
            "comment": title + "; secrets, cookies, authorization and query values are redacted",
            "entries": entries,
        }
    }


def build_api_error_ledger(transactions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for row in transactions:
        response = row.get("response") if isinstance(row.get("response"), Mapping) else {}
        status = response.get("status")
        capture_status = str(response.get("capture_status") or "")
        failed = bool(row.get("error")) or (isinstance(status, int) and status >= 400) or capture_status.startswith("unavailable")
        if not failed:
            continue
        rows.append({
            "transaction_id": row.get("transaction_id"),
            "stage": row.get("stage"),
            "method": row.get("method"),
            "endpoint_template": row.get("endpoint_template"),
            "endpoint_kind": row.get("endpoint_kind"),
            "status": status,
            "capture_status": capture_status,
            "error": mask_sensitive_string(str(row.get("error") or "")),
        })
    return {
        "schema_version": "hip.api-error-ledger.v1",
        "error_count": len(rows),
        "errors": rows,
        "values_stored": False,
    }

def build_input_api_crosswalk(
    *,
    phase: str,
    input_payload: Mapping[str, Any],
    state_graph: Mapping[str, Any],
    observed_contracts: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    phase_data = phase_payload(input_payload, phase)
    phase_flat = flatten_json(phase_data, f"$.objects.{PHASE_OBJECT_KEYS.get(phase, phase)}")
    rows: List[Dict[str, Any]] = []
    mapped_paths: set[str] = set()
    nodes = state_graph.get("nodes") if isinstance(state_graph, Mapping) else []
    for node in nodes or []:
        if not isinstance(node, Mapping):
            continue
        path = str(node.get("input_path") or "")
        action = str(node.get("action") or "")
        if not path or action in {"verify_only", "noop", "skip"}:
            continue
        value = _get_json_path(input_payload, path)
        mapped_paths.add(path)
        rows.append({
            "node_id": node.get("node_id"),
            "input_path": path,
            "field_key": node.get("field_key"),
            "section": node.get("section"),
            "row_kind": node.get("row_kind"),
            "row_index": node.get("row_index"),
            "framework_names": list((node.get("semantic_locator") or {}).get("names") or []),
            "expected_type": type_shape(value),
            "value_hash": _hash(mask_sensitive_data(value), 16),
            "value_persisted": False,
        })

    request_leaf_keys: set[str] = set()
    for contract in observed_contracts:
        for example in contract.get("request_examples_redacted") or []:
            if isinstance(example, (dict, list)):
                request_leaf_keys.update(_norm(path.rsplit(".", 1)[-1].split("[")[0]) for path in flatten_json(example))

    for row in rows:
        candidates = {_norm(row.get("field_key"))}
        candidates.update(_norm(name) for name in row.get("framework_names") or [])
        leaf = _norm(str(row.get("input_path") or "").rsplit(".", 1)[-1].split("[")[0])
        candidates.add(leaf)
        row["observed_api_key_matches"] = sorted(x for x in candidates if x and x in request_leaf_keys)
        row["api_key_match"] = bool(row["observed_api_key_matches"])

    missing = sorted(path for path in phase_flat if path not in mapped_paths and not path.startswith("$._"))
    return {
        "schema_version": "hip.ui-api-input-crosswalk.v1",
        "phase": phase,
        "phase_object_key": PHASE_OBJECT_KEYS.get(phase),
        "state_graph_node_count": len(nodes or []),
        "mapped_node_count": len(rows),
        "rows": rows,
        "unmapped_phase_input_leaf_paths": missing,
        "observed_request_leaf_keys": sorted(request_leaf_keys),
        "values_persisted": False,
    }


def build_openapi_document(contracts: Sequence[Mapping[str, Any]], *, title: str) -> Dict[str, Any]:
    paths: Dict[str, Any] = {}
    for contract in contracts:
        template = str(contract.get("endpoint_template") or "")
        parsed = urlparse(template)
        path = parsed.path or "/"
        method = str(contract.get("method") or "get").lower()
        operation = {
            "operationId": f"hip_{contract.get('contract_id')}",
            "summary": f"Observed HIP {contract.get('endpoint_kind')} contract",
            "x-observed-host": parsed.hostname or "",
            "x-observed-statuses": contract.get("statuses") or [],
            "x-safe-to-replay-automatically": bool(contract.get("safe_to_replay_automatically")),
            "x-mutation-capable": bool(contract.get("mutation_capable")),
            "responses": {str(status): {"description": "Observed response"} for status in contract.get("statuses") or ["default"]},
        }
        shapes = contract.get("request_shapes") or []
        if shapes:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": {"x-observed-shape": shapes[0]}}},
            }
        response_shapes = contract.get("response_shapes") or []
        if response_shapes:
            operation["x-observed-response-shape"] = response_shapes[0]
        paths.setdefault(path, {})[method] = operation
    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": "1.0.0", "description": "Generated from authenticated HIP browser traffic; secrets and customer values are redacted."},
        "paths": paths,
        "x-generation-policy": "observed traffic only; no endpoint invented by the LLM",
    }


def build_postman_collection(contracts: Sequence[Mapping[str, Any]], *, name: str) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for contract in contracts:
        url = (contract.get("observed_urls") or [contract.get("endpoint_template") or ""])[0]
        request: Dict[str, Any] = {
            "method": contract.get("method") or "GET",
            "header": [{"key": "Authorization", "value": "Bearer {{HIP_API_TOKEN}}", "type": "text"}],
            "url": {"raw": url, "host": [(urlparse(url).hostname or "")], "path": [part for part in urlparse(url).path.split("/") if part]},
            "description": f"Observed as {contract.get('endpoint_kind')}; auto replay safe={contract.get('safe_to_replay_automatically')}",
        }
        examples = contract.get("request_examples_redacted") or []
        if examples:
            request["body"] = {"mode": "raw", "raw": json.dumps(examples[0], indent=2, ensure_ascii=False), "options": {"raw": {"language": "json"}}}
        items.append({"name": f"{contract.get('method')} {urlparse(url).path}", "request": request, "response": []})
    return {
        "info": {"name": name, "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "variable": [{"key": "HIP_API_TOKEN", "value": ""}],
        "item": items,
    }


class FormAPIAgentQRuntime:
    """AgentQ-style UI/API intelligence and optional dual-path executor.

    The deterministic browser form engine remains authoritative. This runtime
    observes every request caused by opening and filling a form, builds API
    contracts, captures a submit request behind a network-abort safety barrier,
    compares UI state with the generated payload, and optionally replays an
    observed request through the authenticated BrowserContext API client.

    The LLM is never allowed to invent an endpoint, HTTP method, selector or
    payload key. Dell AIA/HIP Intelligence may rank already observed contracts.
    """

    SCHEMA_VERSION = "hip.form-api-agentq.v1"

    def __init__(self, *, config: Any, root_dir: str | Path, browser: Any, agentq: Any = None) -> None:
        self.config = config
        self.policy = getattr(config, "api", None)
        self.root_dir = Path(root_dir)
        self.browser = browser
        self.agentq = agentq
        self.enabled = bool(getattr(self.policy, "capture_observed_contracts", True))
        self.dual_ui_api = bool(getattr(self.policy, "dual_ui_api", False))
        self.capture_submit = bool(getattr(self.policy, "capture_submit_payload", False))
        self.mode = str(getattr(self.policy, "execution_mode", "capture") or "capture").lower()
        self.require_capture = bool(getattr(self.policy, "require_capture_for_completion", False))
        self.allow_mutation = bool(getattr(self.policy, "allow_mutating_methods", False))
        self.states: Dict[Tuple[str, int], Dict[str, Any]] = {}
        self.run_records: List[Dict[str, Any]] = []
        self.memory_dir = self.root_dir / "api_agentq_memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root_dir / "form_api_intelligence_manifest.json"
        self._write_manifest()

    def _write_manifest(self) -> None:
        safe_write_json(self.manifest_path, {
            "schema_version": self.SCHEMA_VERSION,
            "enabled": self.enabled,
            "dual_ui_api": self.dual_ui_api,
            "capture_submit_payload": self.capture_submit,
            "execution_mode": self.mode,
            "require_capture_for_completion": self.require_capture,
            "single_browser_owner": True,
            "agentq": {
                "web_representation": True,
                "deterministic_safety_gate": True,
                "actor": "rank observed UI and API actions",
                "critic": "UI/API parity + response + judge outcome",
                "exploration_exploitation": "UCB-style observed contract preference",
                "trajectory_memory": str(self.memory_dir / "api_trajectories.jsonl"),
                "llm_can_invent_endpoint": False,
                "llm_can_invent_payload_key": False,
            },
            "safety": {
                "submit_capture_blocks_network_mutations": True,
                "write_requires_cli_and_environment_confirmation": True,
                "authorization_persisted": False,
                "cookies_persisted": False,
            },
            "updated_at": utc_now(),
            "attempts": self.run_records[-200:],
        })

    def begin_attempt(
        self,
        *,
        phase: str,
        attempt: int,
        phase_dir: str | Path,
        input_payload: Mapping[str, Any],
        state_graph: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled"}
        phase_dir = Path(phase_dir)
        out_dir = phase_dir / "form_api_intelligence" / f"attempt_{int(attempt):02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "phase": phase,
            "attempt": int(attempt),
            "phase_dir": str(phase_dir),
            "out_dir": str(out_dir),
            "network_start": len(getattr(self.browser, "network_tab_events", []) or []),
            "network_record_start": len(getattr(self.browser, "network_records", []) or []),
            "action_start": len(getattr(self.browser, "action_events", []) or []),
            "started_at": utc_now(),
            "input_payload": input_payload,
            "state_graph": state_graph,
        }
        self.states[(phase, int(attempt))] = state
        safe_write_json(out_dir / "api_capture_start.json", {
            "phase": phase,
            "attempt": int(attempt),
            "network_start": state["network_start"],
            "action_start": state["action_start"],
            "started_at": state["started_at"],
        })
        return {"status": "started", "out_dir": str(out_dir)}

    def _events_for_state(self, state: Mapping[str, Any]) -> List[Dict[str, Any]]:
        events = list(getattr(self.browser, "network_tab_events", []) or [])[int(state.get("network_start", 0)):]
        out = [_event_dict(event) for event in events]
        legacy = list(getattr(self.browser, "network_records", []) or [])[int(state.get("network_record_start", 0)):]
        for event in legacy:
            row = _event_dict(event)
            if row:
                row.setdefault("source", "playwright_response")
                out.append(row)
        return out

    @staticmethod
    def _split_events(events: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        open_events: List[Dict[str, Any]] = []
        fill_events: List[Dict[str, Any]] = []
        for event in events:
            stage = _norm(event.get("stage"))
            if any(token in stage for token in ("find_add", "click_add", "capture_add", "open_", "page_ready")):
                open_events.append(dict(event))
            else:
                fill_events.append(dict(event))
        if not open_events and events:
            # Network event ordering is still useful when older phase modules do
            # not set fine-grained stage names. Treat the first third as form-open.
            split = max(1, len(events) // 3)
            open_events = [dict(x) for x in events[:split]]
            fill_events = [dict(x) for x in events[split:]]
        return open_events, fill_events

    async def _capture_submit_request(self, *, phase: str, out_dir: Path) -> Dict[str, Any]:
        if not self.capture_submit:
            return {"status": "disabled"}
        page = getattr(self.browser, "page", None)
        if page is None:
            return {"status": "unavailable", "reason": "browser page unavailable"}

        try:
            candidate = await page.evaluate("""() => {
              const visible = el => { const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'; };
              const active = [...document.querySelectorAll('.dds__drawer,[role="dialog"],form')].filter(visible).pop() || document.body;
              const rank = text => {
                const t=(text||'').trim().toLowerCase();
                if (t==='create') return 100;
                if (t==='save') return 95;
                if (t==='submit') return 90;
                if (t==='finish') return 80;
                return -1;
              };
              const buttons=[...active.querySelectorAll('button,[role="button"]')].filter(visible)
                .map((el,index)=>({el,index,text:(el.innerText||el.getAttribute('aria-label')||el.title||'').trim(),score:rank(el.innerText||el.getAttribute('aria-label')||el.title||''),disabled:!!(el.disabled||el.getAttribute('aria-disabled')==='true')}))
                .filter(x=>x.score>=0&&!x.disabled).sort((a,b)=>b.score-a.score);
              if(!buttons.length) return {found:false};
              const chosen=buttons[0];
              chosen.el.setAttribute('data-hip-api-capture-submit','true');
              return {found:true,text:chosen.text,score:chosen.score,tag:chosen.el.tagName.toLowerCase()};
            }""")
        except Exception as exc:
            return {"status": "error", "reason": mask_sensitive_string(str(exc))}
        if not isinstance(candidate, Mapping) or not candidate.get("found"):
            return {"status": "not_found", "reason": "No enabled Create/Save/Submit/Finish control on active form"}

        captured: List[Dict[str, Any]] = []
        ephemeral_requests: List[Dict[str, Any]] = []

        async def handler(route: Any, request: Any) -> None:
            method = str(getattr(request, "method", "GET") or "GET").upper()
            if method in MUTATING_METHODS:
                try:
                    body = getattr(request, "post_data_json", None)
                    if callable(body):
                        body = body()
                except Exception:
                    body = None
                if body is None:
                    body = _try_json(getattr(request, "post_data", None)) or getattr(request, "post_data", None)
                try:
                    headers = await request.all_headers()
                except Exception:
                    headers = {}
                raw_url = str(getattr(request, "url", ""))
                public_request = {
                    "url": mask_sensitive_string(raw_url),
                    "method": method,
                    "headers_redacted": mask_sensitive_data(headers),
                    "body_redacted": mask_sensitive_data(body),
                    "body_shape": type_shape(body),
                    "captured_at": utc_now(),
                    "blocked_before_backend": True,
                }
                captured.append(public_request)
                # Exact values are retained only in process memory for an explicitly confirmed
                # API replay. They are never written to disk, Portal Brain, reports, or logs.
                ephemeral_requests.append({
                    "url": raw_url,
                    "method": method,
                    "headers": dict(headers or {}),
                    "body": body,
                })
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        click_error = ""
        try:
            await page.route("**/*", handler)
            try:
                await page.locator('[data-hip-api-capture-submit="true"]').click(timeout=8000)
            except Exception as exc:
                click_error = mask_sensitive_string(str(exc))
            try:
                await page.wait_for_timeout(1200)
            except Exception:
                pass
        finally:
            try:
                await page.unroute("**/*", handler)
            except Exception:
                try:
                    await page.unroute("**/*")
                except Exception:
                    pass
            try:
                await page.evaluate("""() => document.querySelectorAll('[data-hip-api-capture-submit]').forEach(el=>el.removeAttribute('data-hip-api-capture-submit'))""")
            except Exception:
                pass

        result = {
            "status": "captured" if captured else "no_request_captured",
            "phase": phase,
            "button": mask_sensitive_data(candidate),
            "requests": captured,
            "click_error": click_error,
            "backend_mutation_possible": False,
            "safety_proof": "All POST/PUT/PATCH/DELETE requests were aborted by Playwright routing before network delivery.",
        }
        safe_write_json(out_dir / "blocked_submit_api_capture.json", result)
        # Private key is intentionally excluded from every persisted artifact and from
        # the public result returned by finish_attempt.
        if ephemeral_requests:
            result["_ephemeral_requests"] = ephemeral_requests
        return result

    def _rank_contracts(self, contracts: Sequence[Mapping[str, Any]], *, phase: str) -> List[Dict[str, Any]]:
        memory_path = self.memory_dir / "api_trajectories.jsonl"
        prior: Dict[str, Dict[str, float]] = {}
        if memory_path.is_file():
            try:
                for line in memory_path.read_text(encoding="utf-8").splitlines()[-5000:]:
                    row = json.loads(line)
                    key = str(row.get("contract_id") or "")
                    if not key:
                        continue
                    p = prior.setdefault(key, {"uses": 0.0, "reward": 0.0})
                    p["uses"] += 1.0
                    p["reward"] += float(row.get("reward") or 0.0)
            except Exception:
                prior = {}
        total = 1.0 + sum(v["uses"] for v in prior.values())
        ranked: List[Dict[str, Any]] = []
        for contract in contracts:
            cid = str(contract.get("contract_id") or "")
            p = prior.get(cid, {"uses": 0.0, "reward": 0.0})
            success_rate = p["reward"] / p["uses"] if p["uses"] else 0.0
            exploration = ((2.0 * max(0.0, __import__("math").log(total))) / (1.0 + p["uses"])) ** 0.5
            kind = str(contract.get("endpoint_kind") or "")
            info = 0.9 if kind in {"create", "update"} else 0.75 if kind == "validation" else 0.5 if kind in {"reference_or_read", "read"} else 0.3
            score = info + (0.35 * success_rate) + (0.15 * exploration)
            ranked.append({
                "contract_id": cid,
                "method": contract.get("method"),
                "endpoint_template": contract.get("endpoint_template"),
                "endpoint_kind": kind,
                "actor_score": round(score, 6),
                "critic_prior_reward": round(success_rate, 6),
                "ucb_exploration_bonus": round(exploration, 6),
                "safe": bool(contract.get("safe_to_replay_automatically")),
                "observed_only": True,
            })
        return sorted(ranked, key=lambda row: row["actor_score"], reverse=True)

    async def _execute_api_plan(self, *, submit_capture: Mapping[str, Any], contracts: Sequence[Mapping[str, Any]], out_dir: Path) -> Dict[str, Any]:
        plan = {
            "mode": self.mode,
            "status": "capture_only",
            "request": None,
            "response": None,
            "mutation_sent": False,
            "confirmation_required": "HIP_ALLOW_API_MUTATION=YES",
        }
        captured = list(submit_capture.get("requests") or []) if isinstance(submit_capture, Mapping) else []
        ephemeral = list(submit_capture.get("_ephemeral_requests") or []) if isinstance(submit_capture, Mapping) else []
        selected_public: Optional[Dict[str, Any]] = dict(captured[0]) if captured else None
        selected_exact: Optional[Dict[str, Any]] = dict(ephemeral[0]) if ephemeral else None
        selected: Optional[Dict[str, Any]] = selected_public
        if self.mode in {"capture", "dry_run"}:
            plan["request"] = selected
            plan["status"] = "captured" if selected else "no_mutation_request_observed"
            safe_write_json(out_dir / "api_execution_result.json", plan)
            return plan

        if self.mode == "validate":
            validation = next((c for c in contracts if c.get("endpoint_kind") == "validation"), None)
            if validation:
                examples = validation.get("request_examples_redacted") or []
                plan.update({
                    "status": "validated_from_observed_ui_traffic",
                    "request": {
                        "url": (validation.get("observed_urls") or [validation.get("endpoint_template")])[0],
                        "method": validation.get("method"),
                        "body_redacted": examples[0] if examples else None,
                        "validation_only": True,
                    },
                    "response": {
                        "observed_statuses": list(validation.get("statuses") or []),
                        "observed_response_shapes": list(validation.get("response_shapes") or []),
                    },
                    "mutation_sent": False,
                })
            else:
                plan["status"] = "no_validation_endpoint_observed"
            safe_write_json(out_dir / "api_execution_result.json", plan)
            return plan

        if self.mode == "write":
            confirmed = self.allow_mutation and os.getenv(str(getattr(self.policy, "mutation_confirmation_env_var", "HIP_ALLOW_API_MUTATION")), "").strip().upper() == "YES"
            if not confirmed:
                plan["status"] = "blocked_missing_explicit_mutation_confirmation"
                plan["request"] = selected
                safe_write_json(out_dir / "api_execution_result.json", plan)
                return plan
            if selected_exact is None:
                plan["status"] = "blocked_exact_in_memory_request_unavailable"
                plan["request"] = selected
                safe_write_json(out_dir / "api_execution_result.json", plan)
                return plan

        if not selected:
            plan["status"] = "no_observed_request_available"
            safe_write_json(out_dir / "api_execution_result.json", plan)
            return plan

        context = getattr(self.browser, "context", None)
        request_context = getattr(context, "request", None) if context is not None else None
        if request_context is None:
            plan["status"] = "authenticated_browser_api_context_unavailable"
            plan["request"] = selected
            safe_write_json(out_dir / "api_execution_result.json", plan)
            return plan

        exact_request = selected_exact or selected
        url = str(exact_request.get("url") or selected.get("url") or "")
        method = str(exact_request.get("method") or selected.get("method") or "POST").upper()
        body = exact_request.get("body") if selected_exact else selected.get("body_redacted")
        raw_headers = exact_request.get("headers") if selected_exact else {}
        headers: Dict[str, str] = {}
        # Use the exact observed authenticated request headers only in memory. Browser-managed
        # transport headers are omitted; authorization/correlation headers are allowed for the
        # immediate replay but are never persisted in the execution result.
        blocked_header_names = {
            "cookie", "host", "content-length", "connection", "accept-encoding",
            "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-ch-ua",
            "sec-ch-ua-mobile", "sec-ch-ua-platform", "user-agent",
        }
        if isinstance(raw_headers, Mapping):
            for key, value in raw_headers.items():
                name = str(key or "").strip()
                if not name or name.lower() in blocked_header_names:
                    continue
                headers[name] = str(value)
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")
        try:
            response = await request_context.fetch(url, method=method, data=body, headers=headers, timeout=90000)
            try:
                text = await response.text()
            except Exception:
                text = ""
            parsed = _try_json(text)
            plan.update({
                "status": "executed",
                "request": mask_sensitive_data(selected),
                "response": {
                    "status": response.status,
                    "ok": bool(response.ok),
                    "body_redacted": mask_sensitive_data(parsed if parsed is not None else text[:10000]),
                },
                "mutation_sent": self.mode == "write" and method in MUTATING_METHODS,
            })
        except Exception as exc:
            plan.update({"status": "execution_failed", "request": mask_sensitive_data(selected), "error": mask_sensitive_string(str(exc))})
        safe_write_json(out_dir / "api_execution_result.json", plan)
        return plan

    def _record_memory(self, *, phase: str, ranked: Sequence[Mapping[str, Any]], pass_: bool, execution: Mapping[str, Any]) -> None:
        path = self.memory_dir / "api_trajectories.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        selected = ranked[0] if ranked else {}
        row = {
            "schema_version": "hip.api-trajectory.v1",
            "timestamp": utc_now(),
            "phase": phase,
            "contract_id": selected.get("contract_id"),
            "endpoint_template": selected.get("endpoint_template"),
            "endpoint_kind": selected.get("endpoint_kind"),
            "action": self.mode,
            "outcome": execution.get("status"),
            "reward": 1.0 if pass_ else -0.5,
            "values_stored": False,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(mask_sensitive_data(row), ensure_ascii=False, sort_keys=True, default=str) + "\n")

    async def finish_attempt(
        self,
        *,
        phase: str,
        attempt: int,
        success: bool,
        verification: Mapping[str, Any],
        judge_result: Mapping[str, Any],
        error: str = "",
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "pass": True}
        state = self.states.get((phase, int(attempt)))
        if not state:
            return {"status": "missing_begin_state", "pass": not self.require_capture}
        if state.get("finished_result"):
            return dict(state["finished_result"])

        out_dir = Path(str(state["out_dir"]))
        events = self._events_for_state(state)
        open_events, fill_events = self._split_events(events)
        all_contracts = build_api_contracts(events, max_contracts=int(getattr(self.policy, "max_observed_contracts_per_phase", 500)))
        open_contracts = build_api_contracts(open_events, max_contracts=500)
        fill_contracts = build_api_contracts(fill_events, max_contracts=500)
        capture_transactions = bool(getattr(self.policy, "capture_request_response_transactions", True))
        max_transactions = int(getattr(self.policy, "max_observed_transactions_per_phase", 2000))
        all_transactions = build_api_transactions(events, max_transactions=max_transactions) if capture_transactions else []
        open_transactions = build_api_transactions(open_events, max_transactions=max(1, max_transactions // 2)) if capture_transactions else []
        fill_transactions = build_api_transactions(fill_events, max_transactions=max_transactions) if capture_transactions else []
        payload_response_index = build_payload_response_index(all_transactions)
        action_events = list(getattr(self.browser, "action_events", []) or [])[int(state.get("action_start", 0)):]
        causal_trace = build_action_api_causal_trace(action_events, all_transactions)
        api_error_ledger = build_api_error_ledger(all_transactions)
        input_payload = state.get("input_payload") if isinstance(state.get("input_payload"), Mapping) else {}
        state_graph = state.get("state_graph") if isinstance(state.get("state_graph"), Mapping) else {}
        crosswalk = build_input_api_crosswalk(
            phase=phase,
            input_payload=input_payload,
            state_graph=state_graph,
            observed_contracts=all_contracts,
        )

        safe_write_json(out_dir / "form_open_api_contracts.json", {"phase": phase, "contracts": open_contracts, "event_count": len(open_events)})
        safe_write_json(out_dir / "ui_fill_api_contracts.json", {"phase": phase, "contracts": fill_contracts, "event_count": len(fill_events)})
        safe_write_json(out_dir / "phase_api_catalog.json", {"phase": phase, "contracts": all_contracts, "event_count": len(events)})
        safe_write_json(out_dir / "form_open_api_transactions.json", {"phase": phase, "transactions": open_transactions, "event_count": len(open_events)})
        safe_write_json(out_dir / "ui_fill_api_transactions.json", {"phase": phase, "transactions": fill_transactions, "event_count": len(fill_events)})
        safe_write_json(out_dir / "form_api_transactions.json", {"phase": phase, "transactions": all_transactions, "event_count": len(events)})
        safe_write_json(out_dir / "api_payload_response_index.json", {"phase": phase, **payload_response_index})
        safe_write_json(out_dir / "ui_api_causal_trace.json", {"phase": phase, **causal_trace})
        safe_write_json(out_dir / "redacted_network.har.json", build_redacted_har(all_transactions, title=f"HIP {phase} observed form traffic"))
        safe_write_json(out_dir / "api_error_ledger.json", {"phase": phase, **api_error_ledger})
        safe_write_json(out_dir / "ui_api_input_crosswalk.json", crosswalk)
        safe_write_json(out_dir / "observed_openapi.json", build_openapi_document(all_contracts, title=f"HIP {phase} observed APIs"))
        safe_write_json(out_dir / "postman_collection.json", build_postman_collection(all_contracts, name=f"HIP {phase} observed APIs"))
        safe_write_json(out_dir / "shadow_phase_payload.redacted.json", mask_sensitive_data(phase_payload(input_payload, phase)))

        submit_capture: Dict[str, Any] = {"status": "not_attempted"}
        if success and bool(judge_result.get("pass", True)) and self.dual_ui_api:
            submit_capture = await self._capture_submit_request(phase=phase, out_dir=out_dir)
        ranked = self._rank_contracts(all_contracts, phase=phase)
        safe_write_json(out_dir / "agentq_api_action_ranking.json", {
            "schema_version": "hip.agentq-api-action-ranking.v1",
            "phase": phase,
            "ranked_observed_contracts": ranked,
            "actor_critic_policy": "deterministic information value + prior reward + UCB exploration bonus",
            "invented_contracts": False,
        })
        execution = await self._execute_api_plan(submit_capture=submit_capture, contracts=all_contracts, out_dir=out_dir) if self.dual_ui_api else {"status": "ui_only", "mutation_sent": False}
        public_submit_capture_for_bundle = {k: v for k, v in submit_capture.items() if not str(k).startswith("_ephemeral")}
        safe_write_json(out_dir / "form_api_payload_response_bundle.json", {
            "schema_version": "hip.form-api-payload-response-bundle.v1",
            "phase": phase,
            "attempt": int(attempt),
            "form_open_transactions": open_transactions,
            "ui_fill_transactions": fill_transactions,
            "all_transactions": all_transactions,
            "coverage": payload_response_index,
            "ui_api_causal_trace": causal_trace,
            "api_error_ledger": api_error_ledger,
            "submit_request_capture": public_submit_capture_for_bundle,
            "submit_response": execution.get("response"),
            "submit_response_note": (
                "Capture mode aborts the mutation before Dell receives it, so no submit response exists. "
                "Reference, option, validation and UI-fill API responses are captured above. "
                "A submit response is available only in explicitly confirmed write mode."
                if public_submit_capture_for_bundle.get("requests") and not execution.get("response") else ""
            ),
            "authorization_persisted": False,
            "cookies_persisted": False,
        })

        required_node_count = sum(1 for row in crosswalk.get("rows") or [] if row.get("input_path"))
        contract_evidence = bool(all_contracts or submit_capture.get("requests"))
        ui_exact = str(verification.get("status") or "").lower() not in {"failed", "fail", "blocked"}
        judge_pass = bool(judge_result.get("pass", True))
        api_response_evidence_pass = bool(payload_response_index.get("response_status_count", 0) > 0)
        api_capture_pass = (contract_evidence and api_response_evidence_pass) if self.require_capture else True
        execution_pass = execution.get("status") not in {"execution_failed", "blocked_missing_explicit_mutation_confirmation"}
        if self.mode == "write":
            execution_pass = execution.get("status") == "executed" and bool((execution.get("response") or {}).get("ok"))
        pass_ = bool((not success or ui_exact) and judge_pass and api_capture_pass and execution_pass and required_node_count > 0)

        public_submit_capture = {k: v for k, v in submit_capture.items() if not str(k).startswith("_ephemeral")}
        result = {
            "schema_version": self.SCHEMA_VERSION,
            "phase": phase,
            "attempt": int(attempt),
            "status": "pass" if pass_ else "blocked",
            "pass": pass_,
            "ui_exact": ui_exact,
            "judge_pass": judge_pass,
            "observed_api_contract_count": len(all_contracts),
            "form_open_contract_count": len(open_contracts),
            "ui_fill_contract_count": len(fill_contracts),
            "captured_submit_request_count": len(submit_capture.get("requests") or []),
            "api_transaction_count": len(all_transactions),
            "api_request_payload_count": payload_response_index.get("request_payload_count"),
            "api_response_status_count": payload_response_index.get("response_status_count"),
            "api_response_payload_count": payload_response_index.get("response_payload_count"),
            "api_payload_response_coverage": payload_response_index,
            "api_response_evidence_pass": api_response_evidence_pass,
            "submit_capture": public_submit_capture,
            "api_execution": execution,
            "crosswalk": {
                "mapped_node_count": crosswalk.get("mapped_node_count"),
                "unmapped_phase_input_leaf_count": len(crosswalk.get("unmapped_phase_input_leaf_paths") or []),
            },
            "agentq_ranked_contracts": ranked[:20],
            "failure": mask_sensitive_string(error),
            "artifacts": {
                "form_open_contracts": str(out_dir / "form_open_api_contracts.json"),
                "ui_fill_contracts": str(out_dir / "ui_fill_api_contracts.json"),
                "phase_api_catalog": str(out_dir / "phase_api_catalog.json"),
                "form_open_transactions": str(out_dir / "form_open_api_transactions.json"),
                "ui_fill_transactions": str(out_dir / "ui_fill_api_transactions.json"),
                "all_transactions": str(out_dir / "form_api_transactions.json"),
                "payload_response_index": str(out_dir / "api_payload_response_index.json"),
                "payload_response_bundle": str(out_dir / "form_api_payload_response_bundle.json"),
                "openapi": str(out_dir / "observed_openapi.json"),
                "postman": str(out_dir / "postman_collection.json"),
                "crosswalk": str(out_dir / "ui_api_input_crosswalk.json"),
                "shadow_payload": str(out_dir / "shadow_phase_payload.redacted.json"),
                "ui_api_causal_trace": str(out_dir / "ui_api_causal_trace.json"),
                "redacted_har": str(out_dir / "redacted_network.har.json"),
                "api_error_ledger": str(out_dir / "api_error_ledger.json"),
            },
            "safety": {
                "submit_request_blocked_before_backend": bool(submit_capture.get("requests")),
                "mutation_sent": bool(execution.get("mutation_sent")),
                "authorization_persisted": False,
                "cookies_persisted": False,
            },
        }
        safe_write_json(out_dir / "form_api_agentq_result.json", result)
        state["finished_result"] = result
        self.run_records.append({"phase": phase, "attempt": int(attempt), "status": result["status"], "result": str(out_dir / "form_api_agentq_result.json")})
        if success and judge_pass:
            self._record_memory(phase=phase, ranked=ranked, pass_=pass_, execution=execution)
        self._write_manifest()
        return result

    def write_run_summary(self) -> Dict[str, Any]:
        phase_results: Dict[str, Dict[str, Any]] = {}
        for state in self.states.values():
            result = state.get("finished_result")
            if isinstance(result, Mapping):
                phase = str(result.get("phase") or "")
                previous = phase_results.get(phase)
                if previous is None or int(result.get("attempt") or 0) >= int(previous.get("attempt") or 0):
                    phase_results[phase] = dict(result)
        payload = {
            "schema_version": "hip.form-api-agentq-run-summary.v1",
            "generated_at": utc_now(),
            "enabled": self.enabled,
            "dual_ui_api": self.dual_ui_api,
            "execution_mode": self.mode,
            "phase_results": phase_results,
            "all_phases_pass": bool(phase_results) and all(bool(row.get("pass")) for row in phase_results.values()),
            "manifest": str(self.manifest_path),
            "trajectory_memory": str(self.memory_dir / "api_trajectories.jsonl"),
            "values_stored_in_reusable_memory": False,
        }
        path = self.root_dir / "form_api_intelligence_summary.json"
        safe_write_json(path, payload)
        payload["path"] = str(path)
        return payload
