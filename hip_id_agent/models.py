from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class NetworkRecord:
    """Compact network response record kept for backwards compatibility."""
    url: str
    method: str
    status: Optional[int]
    request_post_data: Optional[str] = None
    response_body: Optional[str] = None
    content_type: Optional[str] = None
    error: Optional[str] = None


@dataclass
class NetworkTabEvent:
    """Network-tab style event captured from Playwright/CDP.

    Headers and bodies in persisted instances are redacted. response_body_text_redacted is
    capped by config.extraction.max_network_body_chars.
    """
    request_id: str
    url: str
    method: str
    status: Optional[int] = None
    resource_type: str = "unknown"
    request_headers: Dict[str, Any] = field(default_factory=dict)
    response_headers: Dict[str, Any] = field(default_factory=dict)
    request_body_redacted: Any = None
    response_body_redacted: Any = None
    response_body_text_redacted: Optional[str] = None
    response_body_capture_status: str = ""
    response_body_truncated: bool = False
    encoded_data_length: Optional[int] = None
    mime_type: Optional[str] = None
    timestamp: str = field(default_factory=utc_now)
    page_context: str = ""
    stage: str = ""
    error: Optional[str] = None
    initiator: Any = None
    source: str = "playwright_cdp"


@dataclass
class ClickEvent:
    """A click observed either from automation or from browser DOM listener."""
    timestamp: str
    source: str
    url: str
    action: str
    selector: Optional[str] = None
    text: Optional[str] = None
    tag: Optional[str] = None
    element_id: Optional[str] = None
    classes: Optional[str] = None
    href: Optional[str] = None
    role: Optional[str] = None
    aria_label: Optional[str] = None
    bounding_box: Dict[str, Any] = field(default_factory=dict)
    screenshot: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionEvent:
    """First-class browser/action evidence for navigate/click/fill/search/press/wait/extract/verify."""
    action_id: str
    type: Literal["navigate", "click", "fill", "search", "press", "wait", "extract", "verify", "screenshot", "failure"]
    target: str
    value_redacted: Optional[str] = None
    value_hash: Optional[str] = None
    page_url_before: str = ""
    page_url_after: str = ""
    timestamp_start: str = field(default_factory=utc_now)
    timestamp_end: str = ""
    success: bool = True
    error: Optional[str] = None
    screenshot_before: Optional[str] = None
    screenshot_after: Optional[str] = None
    dom_excerpt: Optional[str] = None
    network_events_triggered: List[str] = field(default_factory=list)
    network_event_start_index: int = 0
    network_event_end_index: int = 0
    network_request_ids_before: List[str] = field(default_factory=list)
    stage: str = ""
    backend: str = "playwright"
    execution_provenance: Dict[str, Any] = field(default_factory=dict)
    was_secret: bool = False


@dataclass
class ExtractedID:
    object_type: str
    query: str
    object_id: str
    name: Optional[str]
    source: str
    confidence: float
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IDCandidate:
    target_type: Literal["partner", "system", "account", "unknown"]
    candidate_id: str
    candidate_name: Optional[str]
    source_type: Literal["network", "dom_table", "dom_detail", "url", "text", "memory", "manual"]
    source_url: Optional[str] = None
    endpoint_url: Optional[str] = None
    field_path: Optional[str] = None
    surrounding_text: Optional[str] = None
    matched_query: Optional[str] = None
    confidence: float = 0.0
    evidence_reasons: List[str] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)
    raw_excerpt: Optional[Any] = None


@dataclass
class VerifiedIDResult:
    accepted: bool
    id_value: Optional[str]
    confidence: float
    reasons: List[str] = field(default_factory=list)
    evidence: List[IDCandidate] = field(default_factory=list)
    rejected_candidates: List[IDCandidate] = field(default_factory=list)
    recovery_suggestions: List[str] = field(default_factory=list)
    ambiguous: bool = False
    needs_secondary_verification: bool = False


@dataclass
class StageResult:
    stage: str
    status: str
    message: str
    started_at: str
    finished_at: str
    screenshots: List[str] = field(default_factory=list)
    extracted_ids: List[ExtractedID] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunContext:
    run_id: str
    customer: str
    partner_query: str
    system_query: str
    run_dir: Path
    screenshots_dir: Path
    started_at: str = field(default_factory=utc_now)
    stage_results: List[StageResult] = field(default_factory=list)
    registry: Dict[str, Any] = field(default_factory=dict)
