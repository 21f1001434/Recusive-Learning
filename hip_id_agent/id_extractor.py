from __future__ import annotations

import json
import re
from dataclasses import asdict
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .aia_client import AIAClient
from .models import ExtractedID, IDCandidate, NetworkRecord, NetworkTabEvent, VerifiedIDResult
from .security import mask_sensitive_data, safe_json_loads

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
NUMERIC_ID_RE = re.compile(r"\b\d{3,}\b")
ALNUM_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{4,}")
KEY_VALUE_RE = re.compile(r"(?i)(partner\s*id|partnerId|partner_id|partnerID|system\s*id|systemId|system_id|systemID|domainId|domain_id|account\s*id|accountId|account_id|id|identifier|uuid)\s*[:#=-]?\s*([A-Za-z0-9_.:-]{3,})")

STRONG_ID_FIELDS = {
    "partner": {"partnerid", "partner_id", "partnerID", "partnerId"},
    "system": {"systemid", "system_id", "systemID", "systemId", "domainid", "domain_id", "domainId"},
    "account": {"accountid", "account_id", "accountId", "xaccountid", "x-account-id"},
}
GENERIC_ID_FIELDS = {"id", "uuid", "identifier"}
NAME_FIELDS = {
    "partner": {"partnername", "partner_name", "name", "displayname", "description", "code"},
    "system": {"systemname", "system_name", "domainname", "domain_name", "name", "displayname", "description", "code"},
    "account": {"accountname", "account_name", "name", "displayname", "code"},
}
ENDPOINT_HINTS = {
    # Keep Partner and Account endpoint domains separate. BizLink /partner may render
    # a Manage Account UI and call both /authz/accounts and /authz/partners, but
    # an account row ID is not the same as a partner row ID. Partner extraction must
    # prefer /partners payloads; /accounts payloads are saved under object_type=account.
    "partner": ["/partner", "/partners", "partnerid", "partner-name", "partnername"],
    "system": ["/system", "/systems", "systemid", "domain", "system-name", "systemname"],
    "account": ["/account", "/accounts", "/authz/accounts", "accountid", "accountname", "x-account-id"],
}
WRONG_DOMAIN_HINTS = {
    "partner": ["/system", "/systems", "systemid", "domainid", "domain_id", "system-name", "/accounts", "/authz/accounts", "accountname"],
    "system": ["/partner", "/partners", "partnerid", "partner-name", "/accounts", "/authz/accounts", "accountname"],
    "account": ["/partner", "/partners", "partnerid", "systemid", "domainid", "system-name"],
}


def _canon_key(k: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k or "").lower())


def normalize(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (s or "").strip().upper()).strip()


def compact_name(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())


def _compact_json(value: Any, limit: int = 1400) -> str:
    text = json.dumps(mask_sensitive_data(value), ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _find_case_key(d: Dict[str, Any], keys: Iterable[str]) -> Tuple[Optional[str], Any]:
    wanted = {_canon_key(k): k for k in keys}
    for k, v in d.items():
        ck = _canon_key(k)
        if ck in wanted and v not in (None, ""):
            return str(k), v
    return None, None


def _endpoint_has_domain(url: Optional[str], target_type: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in ENDPOINT_HINTS.get(target_type, []))


def _endpoint_has_wrong_domain(url: Optional[str], target_type: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in WRONG_DOMAIN_HINTS.get(target_type, []))


def _endpoint_domain_kind(url: Optional[str]) -> str:
    """Classify BizLink endpoint kind from URL for safer Partner/Account separation.

    Important: BizLink has nested account routes such as
    ``/authz/accounts/<account-id>/partners``. Those responses contain Partner rows,
    not Account rows. Check the most-specific child resource (partners/systems) before
    the broader parent resource (accounts), otherwise a Partner like AS2TEST opened
    from inside a Gmail Account details page can be misclassified as an account.
    """
    u = (url or "").lower()
    if "/partners" in u or u.endswith("partners") or "partnerid" in u or "partner-name" in u:
        return "partner"
    if "/systems" in u or "/system" in u or "/domains" in u or "systemid" in u or "domain" in u:
        return "system"
    if "/authz/accounts" in u or "/accounts" in u or u.endswith("accounts") or "accountname" in u:
        return "account"
    if "/partner" in u:
        return "partner"
    return "unknown"


def _parent_context_from_url(url: Optional[str]) -> Dict[str, str]:
    """Return parent Account/System context for nested BizLink endpoints when present."""
    out: Dict[str, str] = {}
    if not url:
        return out
    try:
        parsed = urlparse(url)
        path = parsed.path
        m = re.search(r"/accounts/([^/]+)/(?:partners|domains|systems)(?:/|$)", path, flags=re.I)
        if m:
            out["parent_account_id"] = m.group(1)
        qs = parse_qs(parsed.query)
        for key in ["accountId", "account_id", "accountName", "account_name", "parentAccountId"]:
            vals = qs.get(key) or qs.get(key.lower())
            if vals:
                out[key] = vals[0]
    except Exception:
        return out
    return out


def _name_field(row: Dict[str, Any], target_type: str) -> Any:
    _, val = _find_case_key(row, NAME_FIELDS.get(target_type, set()))
    return val


def _query_match_score(text: str | None, query: str, aliases: Optional[List[str]] = None) -> Tuple[float, List[str]]:
    aliases = aliases or []
    hay = normalize(text)
    hay_compact = compact_name(text)
    qs = [query, *aliases]
    reasons: List[str] = []
    best = 0.0
    for q in qs:
        qn = normalize(q)
        qc = compact_name(q)
        if not qn and not qc:
            continue
        if hay == qn:
            return 1.0, ["exact normalized query match"]
        if qn and qn in hay:
            best = max(best, 0.92)
            reasons.append("case/punctuation-insensitive contains match")
        if qc and hay_compact:
            if qc in hay_compact or hay_compact in qc:
                if qc == hay_compact:
                    best = max(best, 1.0)
                    reasons.append("exact compact entity-name match")
                else:
                    # A substring such as AS2TEST inside SCG_AS2_TEST_IN is useful evidence,
                    # but it is not strong enough to auto-save if an exact AS2TEST record exists.
                    length_ratio = min(len(qc), len(hay_compact)) / max(len(qc), len(hay_compact), 1)
                    score = 0.78 if length_ratio >= 0.60 else 0.70
                    best = max(best, score)
                    reasons.append(f"compact substring match {score:.2f}")
            else:
                # Handles common business-name variants such as UHAL vs U-HAUL_PC.
                # Compare against compact tokens too so one long row does not dilute similarity.
                compact_tokens = [t for t in re.split(r"[^A-Z0-9]+", (text or "").upper()) if t]
                compact_tokens += re.findall(r"[A-Z0-9]{3,}", hay_compact)[:50]
                ratios = [SequenceMatcher(None, qc, compact_name(tok)).ratio() for tok in compact_tokens if compact_name(tok)]
                ratio = max(ratios or [SequenceMatcher(None, qc, hay_compact[: max(len(qc) + 8, 16)]).ratio()])
                if ratio >= 0.88:
                    best = max(best, 0.90)
                    reasons.append(f"fuzzy compact business-name match {ratio:.2f}")
                elif ratio >= 0.78:
                    best = max(best, 0.82)
                    reasons.append(f"fuzzy compact business-name match {ratio:.2f}")
        q_tokens = set(qn.split())
        h_tokens = set(hay.split())
        if q_tokens:
            overlap = len(q_tokens & h_tokens) / max(len(q_tokens), 1)
            if overlap >= 0.8:
                best = max(best, 0.86)
                reasons.append(f"high token overlap {overlap:.2f}")
            elif overlap >= 0.5:
                best = max(best, 0.62)
                reasons.append(f"partial token overlap {overlap:.2f}")
    return best, sorted(set(reasons))


class StrictIDValidator:
    def __init__(self, aliases: Optional[List[str]] = None) -> None:
        self.aliases = aliases or []

    def validate_candidate(self, candidate: IDCandidate, query: str, target_type: str) -> VerifiedIDResult:
        reasons = list(candidate.evidence_reasons)
        rejected = list(candidate.rejection_reasons)
        target_type = target_type.lower().strip()
        score = float(candidate.confidence or 0.0)

        if candidate.target_type not in {target_type, "unknown"}:
            rejected.append(f"candidate target_type={candidate.target_type} does not match requested target_type={target_type}")
        endpoint_kind = _endpoint_domain_kind(candidate.endpoint_url or candidate.source_url)
        if endpoint_kind in {"partner", "account", "system"} and endpoint_kind != target_type:
            rejected.append(f"endpoint kind {endpoint_kind} does not match requested target_type={target_type}")
        if not self._looks_like_id(candidate.candidate_id):
            rejected.append("candidate_id does not look like a valid HIP/BizLink ID")
        if endpoint_kind != target_type and _endpoint_has_wrong_domain(candidate.endpoint_url or candidate.source_url, target_type):
            rejected.append("endpoint/domain context belongs to another entity type")

        endpoint_match = _endpoint_has_domain(candidate.endpoint_url or candidate.source_url, target_type)
        if endpoint_match:
            score += 0.20
            reasons.append(f"endpoint belongs to {target_type} domain")

        field = _canon_key(candidate.field_path or "")
        strong_fields = {_canon_key(x) for x in STRONG_ID_FIELDS.get(target_type, set())}
        generic_fields = {_canon_key(x) for x in GENERIC_ID_FIELDS}
        if any(sf in field for sf in strong_fields):
            score += 0.35
            reasons.append(f"strongly typed {target_type} ID field")
        elif field in generic_fields or field.endswith(".id") or field.endswith("[id]") or field == "id":
            reasons.append("generic id field seen")
            if not endpoint_match:
                rejected.append("generic id is not supported by endpoint context")
        else:
            # text/url candidates can still be accepted through context/name evidence.
            if candidate.source_type in {"text", "url"}:
                score += 0.05

        # Query/entity matching using name and surrounding record/page text.
        # Do not include candidate.matched_query here: it is the user query copied onto
        # the candidate for evidence/debugging. Including it would make unrelated rows
        # appear to match themselves (for example AIC-DCE accepting AIC-GSCM).
        match_text = " ".join(str(x or "") for x in [candidate.candidate_name, candidate.surrounding_text])
        match_score, match_reasons = _query_match_score(match_text, query, self.aliases)
        if match_score >= 0.90:
            score += 0.30
            reasons.extend(match_reasons)
        elif match_score >= 0.80:
            score += 0.22
            reasons.extend(match_reasons)
        elif match_score >= 0.60:
            score += 0.10
            reasons.extend(match_reasons)
        else:
            rejected.append("surrounding record/page text does not match search query")

        if candidate.source_type == "dom_detail":
            score += 0.20
            reasons.append("candidate was found on opened detail page")
        elif candidate.source_type == "dom_table":
            score += 0.14
            reasons.append("candidate was found in matching DOM table row")
        elif candidate.source_type == "network":
            score += 0.10
            reasons.append("candidate was found in captured network response")
        elif candidate.source_type == "url":
            score += 0.08
            reasons.append("candidate was found in URL context")

        score = max(0.0, min(score, 0.99))
        candidate.confidence = score
        candidate.evidence_reasons = sorted(set(reasons))
        candidate.rejection_reasons = sorted(set(rejected))
        accepted = score >= 0.90 and not rejected
        needs_secondary = (0.75 <= score < 0.90) and not rejected
        suggestions: List[str] = []
        if rejected:
            suggestions.append("Open the matching table row/details page and extract a typed partnerId/systemId/domainId field.")
        if needs_secondary:
            suggestions.append("Candidate needs secondary verification from details page or typed API field before saving.")
        return VerifiedIDResult(
            accepted=accepted,
            id_value=candidate.candidate_id if accepted else None,
            confidence=score,
            reasons=sorted(set(reasons)),
            evidence=[candidate],
            rejected_candidates=[candidate] if rejected or not accepted else [],
            recovery_suggestions=sorted(set(suggestions)),
            needs_secondary_verification=needs_secondary,
        )

    def choose(self, candidates: List[IDCandidate], query: str, target_type: str) -> VerifiedIDResult:
        results = [self.validate_candidate(c, query, target_type) for c in candidates]
        accepted = [r for r in results if r.accepted]
        rejected_candidates = [c for r in results for c in r.rejected_candidates]
        suggestions = sorted(set(s for r in results for s in r.recovery_suggestions))
        if not candidates:
            return VerifiedIDResult(False, None, 0.0, reasons=[], evidence=[], rejected_candidates=[], recovery_suggestions=[
                "No ID candidates were found. Check if SSO is complete, search box rendered, or network capture is enabled."
            ])
        if not accepted:
            best = max(results, key=lambda r: r.confidence, default=None)
            return VerifiedIDResult(
                accepted=False,
                id_value=None,
                confidence=best.confidence if best else 0.0,
                reasons=best.reasons if best else [],
                evidence=[c for r in results for c in r.evidence],
                rejected_candidates=rejected_candidates or [c for r in results for c in r.evidence],
                recovery_suggestions=suggestions or ["No candidate met the 0.90 automatic-save threshold."],
                needs_secondary_verification=bool(best and best.needs_secondary_verification),
            )
        # Conflict handling: more than one accepted ID usually means ambiguity unless same ID.
        # Exception: if exactly one accepted candidate has an exact compact name match, prefer it
        # over fuzzy substring matches such as AS2TEST inside SCG_AS2_TEST_IN. This mirrors the
        # BizLink grid/search semantics and prevents broad network captures from selecting the
        # wrong nearby record.
        ids = {r.id_value for r in accepted if r.id_value}
        if len(ids) > 1:
            exact = []
            for r in accepted:
                for c in r.evidence:
                    if compact_name(c.candidate_name or "") == compact_name(query or ""):
                        exact.append(r)
                        break
            exact_ids = {r.id_value for r in exact if r.id_value}
            if len(exact_ids) == 1:
                best_exact = max(exact, key=lambda r: r.confidence)
                fuzzy_rejected = [c for r in accepted if r not in exact for c in r.evidence]
                for c in fuzzy_rejected:
                    c.rejection_reasons = sorted(set([*c.rejection_reasons, "rejected because an exact entity-name match exists for the query"]))
                return VerifiedIDResult(
                    accepted=True,
                    id_value=best_exact.id_value,
                    confidence=best_exact.confidence,
                    reasons=sorted(set([*best_exact.reasons, "exact entity-name match selected over fuzzy alternatives"])),
                    evidence=[c for r in exact for c in r.evidence],
                    rejected_candidates=[*rejected_candidates, *fuzzy_rejected],
                    recovery_suggestions=suggestions,
                )
            return VerifiedIDResult(
                accepted=False,
                id_value=None,
                confidence=max(r.confidence for r in accepted),
                reasons=["multiple conflicting verified candidates were found"],
                evidence=[c for r in accepted for c in r.evidence],
                rejected_candidates=rejected_candidates,
                recovery_suggestions=["Ambiguous ID candidates. Open the exact details page and verify the displayed typed ID before saving."],
                ambiguous=True,
            )
        best = max(accepted, key=lambda r: r.confidence)
        return VerifiedIDResult(
            accepted=True,
            id_value=best.id_value,
            confidence=best.confidence,
            reasons=best.reasons,
            evidence=[c for r in accepted for c in r.evidence],
            rejected_candidates=rejected_candidates,
            recovery_suggestions=suggestions,
        )

    def _looks_like_id(self, value: str) -> bool:
        value = (value or "").strip().strip('"\'')
        if not value or len(value) < 3 or len(value) > 160:
            return False
        if UUID_RE.fullmatch(value):
            return True
        if NUMERIC_ID_RE.fullmatch(value):
            return True
        if ALNUM_ID_RE.fullmatch(value) and any(ch.isdigit() for ch in value):
            return True
        return False


def validate_candidate(candidate: IDCandidate, query: str, target_type: str) -> VerifiedIDResult:
    return StrictIDValidator().validate_candidate(candidate, query, target_type)


class IDExtractor:
    """Strict, target-aware Partner/System ID extractor.

    Generic fields such as `id` are no longer trusted by themselves. A generic ID must be
    supported by endpoint domain evidence and an entity/query match, or by details-page/row
    evidence. Conflicting accepted IDs are marked ambiguous and not saved automatically.
    """

    def __init__(self, aia_client: Optional[AIAClient] = None, minimum_confidence: float = 0.90, aliases: Optional[List[str]] = None):
        self.aia = aia_client
        self.minimum_confidence = max(0.90, float(minimum_confidence or 0.90))
        self.validator = StrictIDValidator(aliases=aliases or [])

    def extract(
        self,
        *,
        object_type: str,
        query: str,
        url: str,
        html: str,
        body_text: str,
        network_records: List[NetworkRecord],
        network_tab_events: Optional[List[NetworkTabEvent]] = None,
    ) -> Tuple[Optional[ExtractedID], Dict[str, Any]]:
        candidates: List[IDCandidate] = []
        candidates.extend(self._from_network_tab_events(object_type, query, network_tab_events or []))
        candidates.extend(self._from_network_records(object_type, query, network_records))
        candidates.extend(self._from_key_value_text(object_type, query, body_text, url=url))
        candidates.extend(self._from_tables(object_type, query, html, url=url))
        candidates.extend(self._from_links_and_url(object_type, query, url, html))
        candidates = self._dedupe_candidates(candidates)
        result = self.validator.choose(candidates, query, object_type)

        best: Optional[ExtractedID] = None
        if result.accepted and result.id_value:
            ev = result.evidence[0] if result.evidence else None
            source_alias = "verified"
            if ev:
                source_alias = {"dom_table": "html_table", "dom_detail": "text_key_value", "text": "text_key_value", "network": "network_tab_json", "url": "url_query_param"}.get(ev.source_type, ev.source_type)
            best = ExtractedID(
                object_type=object_type,
                query=query,
                object_id=result.id_value,
                name=ev.candidate_name if ev else None,
                source=source_alias,
                confidence=result.confidence,
                evidence={
                    "reasons": result.reasons,
                    "source_url": ev.source_url if ev else None,
                    "endpoint_url": ev.endpoint_url if ev else None,
                    "field_path": ev.field_path if ev else None,
                    "candidate_count": len(candidates),
                },
            )

        debug = {
            "candidate_count": len(candidates),
            "candidates": [asdict(c) for c in candidates[:80]],
            "verified_result": asdict(result),
            "accepted_candidates": [asdict(c) for c in result.evidence],
            "rejected_candidates": [asdict(c) for c in result.rejected_candidates],
            "recovery_suggestions": result.recovery_suggestions,
            "aia_used": False,
        }

        # AIA is only a secondary suggestion; it cannot override strict validator.
        if (not best) and self.aia and self.aia.enabled:
            aia_result = self.aia.extract_ids_from_snapshot(
                object_type=object_type,
                query=query,
                snapshot={
                    "url": url,
                    "body_text": body_text[:18000],
                    "strict_candidates": debug["candidates"],
                    "network_tab_events": [asdict(r) for r in (network_tab_events or [])[-40:]],
                    "network_records": [r.__dict__ for r in network_records[-20:]],
                },
            )
            debug["aia_used"] = True
            debug["aia_result"] = mask_sensitive_data(aia_result)
            obj_id = aia_result.get("object_id") if isinstance(aia_result, dict) else None
            name = aia_result.get("name") if isinstance(aia_result, dict) else None
            if obj_id:
                c = IDCandidate(
                    target_type=object_type, candidate_id=str(obj_id), candidate_name=str(name) if name else None,
                    source_type="manual", source_url=url, surrounding_text=_compact_json(aia_result.get("evidence")),
                    matched_query=query, confidence=0.55, evidence_reasons=["AIA suggested candidate; requires strict validation"], raw_excerpt=mask_sensitive_data(aia_result),
                )
                strict = self.validator.choose([c], query, object_type)
                debug["aia_strict_validation"] = asdict(strict)
                if strict.accepted and strict.id_value:
                    best = ExtractedID(object_type, query, strict.id_value, name, "aia_strict_validated", strict.confidence, {"reasons": strict.reasons})
        return best, mask_sensitive_data(debug)

    def extract_candidates_from_network(self, object_type: str, query: str, network_tab_events: List[NetworkTabEvent]) -> List[IDCandidate]:
        return self._dedupe_candidates(self._from_network_tab_events(object_type, query, network_tab_events))

    def extract_all_from_network(self, object_type: str, network_tab_events: List[NetworkTabEvent]) -> List[ExtractedID]:
        # Bulk discovery mode: save entity lists from trusted Partner/System/Domain endpoints.
        # BizLink list payloads often use a generic `id` plus `name`/`systemName`; that is safe
        # for memory indexing only when the endpoint belongs to the target domain and a name exists.
        out: List[ExtractedID] = []
        for ev in network_tab_events:
            cands = self._from_network_tab_events(object_type, "", [ev])
            for c in cands:
                # Bulk discovery must be endpoint-kind strict. A nested URL such as
                # /authz/accounts/<account-id>/partners contains both /accounts and /partners;
                # the child resource decides the row type. Do not save Partner rows as Accounts
                # simply because the parent account appears in the URL.
                if _endpoint_domain_kind(c.endpoint_url) != object_type:
                    continue
                raw_field_path = str(c.field_path or "")
                field_key = _canon_key(raw_field_path)
                strong = any(_canon_key(f) in field_key for f in STRONG_ID_FIELDS.get(object_type, set()))
                generic_with_name = (raw_field_path.lower().endswith(".id") or raw_field_path.lower() == "id" or "[id]" in raw_field_path.lower()) and bool(c.candidate_name)
                if strong or generic_with_name:
                    out.append(ExtractedID(
                        object_type,
                        c.candidate_name or c.candidate_id,
                        c.candidate_id,
                        c.candidate_name,
                        c.source_type,
                        0.90 if generic_with_name else 0.93,
                        {"endpoint_url": c.endpoint_url, "field_path": c.field_path, "bulk_discovery": True},
                    ))
        return self._dedupe(out)

    def _from_network_tab_events(self, object_type: str, query: str, events: List[NetworkTabEvent]) -> List[IDCandidate]:
        out: List[IDCandidate] = []
        for ev in events:
            body = ev.response_body_redacted
            if body is None and ev.response_body_text_redacted:
                body = safe_json_loads(ev.response_body_text_redacted)
            if body is None:
                continue
            out.extend(self._rows_from_json(object_type, query, body, ev.url, source_url=ev.page_context, source_type="network", base_path="response", raw_extra={"status": ev.status, "resource_type": ev.resource_type, "request_id": ev.request_id, "stage": ev.stage}))
        return out

    def _from_network_records(self, object_type: str, query: str, records: List[NetworkRecord]) -> List[IDCandidate]:
        out: List[IDCandidate] = []
        for rec in records:
            if not rec.response_body:
                continue
            data = safe_json_loads(rec.response_body)
            if data is None:
                continue
            out.extend(self._rows_from_json(object_type, query, data, rec.url, source_url=rec.url, source_type="network", base_path="response", raw_extra={"status": rec.status, "content_type": rec.content_type}))
        return out

    def _rows_from_json(self, object_type: str, query: str, data: Any, endpoint_url: str, *, source_url: str, source_type: str, base_path: str, raw_extra: Optional[Dict[str, Any]] = None) -> List[IDCandidate]:
        rows: List[Tuple[Dict[str, Any], str]] = []
        strong = STRONG_ID_FIELDS.get(object_type, set())
        names = NAME_FIELDS.get(object_type, set())

        def collect(node: Any, path: str = base_path) -> None:
            if isinstance(node, list):
                for i, item in enumerate(node):
                    collect(item, f"{path}[{i}]")
            elif isinstance(node, dict):
                has_id_key = any(_canon_key(k) in {_canon_key(x) for x in set(strong) | GENERIC_ID_FIELDS} for k in node.keys())
                has_name_key = any(_canon_key(k) in {_canon_key(x) for x in names} for k in node.keys())
                if has_id_key or has_name_key:
                    rows.append((node, path))
                for k, v in node.items():
                    if isinstance(v, (dict, list)):
                        collect(v, f"{path}.{k}")
        collect(data)
        out: List[IDCandidate] = []
        for row, path in rows:
            # Try target-specific entity ID fields first, then generic ID fields.
            # Some BizLink records have fields like partnerIdentifier/partnerIdentifierValue
            # for DUNS or business identifiers; those are not the internal entity ID and
            # must not prevent us from checking the real generic `id` field.
            id_key = None
            eid = None
            ordered_keys = list(strong) + list(GENERIC_ID_FIELDS)
            for possible in ordered_keys:
                k, v = _find_case_key(row, [possible])
                if v is None:
                    continue
                sv = str(v).strip()
                if self._looks_like_id(sv):
                    id_key, eid = k, sv
                    break
            if eid is None:
                continue
            name = _name_field(row, object_type)
            row_text = _compact_json(row, 2200)
            score, match_reasons = _query_match_score(" ".join([str(name or ""), row_text]), query, self.validator.aliases)
            endpoint_kind = _endpoint_domain_kind(endpoint_url)
            parent_context = _parent_context_from_url(endpoint_url)
            reasons = [*match_reasons]
            if parent_context and object_type == "partner":
                reasons.append("partner payload was loaded under parent account context")
            if endpoint_kind in {"partner", "account", "system"} and endpoint_kind != object_type:
                reasons.append(f"endpoint kind is {endpoint_kind}, not {object_type}")
            if query and compact_name(str(name or "")) == compact_name(query):
                score = max(score, 1.0)
                reasons.append("exact compact entity-name match")
            if endpoint_kind == object_type:
                reasons.append(f"endpoint kind is exactly {object_type}")
            if id_key and _canon_key(id_key) in {_canon_key(x) for x in strong}:
                reasons.append(f"field {id_key} is target-specific")
            if _endpoint_has_domain(endpoint_url, object_type):
                reasons.append("endpoint has target domain hint")
            base_conf = 0.12 + min(score * 0.25, 0.30)
            if endpoint_kind == object_type:
                base_conf += 0.08
            if query and compact_name(str(name or "")) == compact_name(query):
                base_conf += 0.12
            out.append(IDCandidate(
                target_type=object_type,
                candidate_id=eid,
                candidate_name=str(name).strip() if name else None,
                source_type=source_type,  # network
                source_url=source_url,
                endpoint_url=endpoint_url,
                field_path=f"{path}.{id_key}" if id_key else path,
                surrounding_text=row_text,
                matched_query=query if score >= 0.6 else None,
                confidence=base_conf,
                evidence_reasons=reasons,
                raw_excerpt={"row_keys": list(row.keys())[:60], **parent_context, **(raw_extra or {})},
            ))
        return out

    def _from_key_value_text(self, object_type: str, query: str, text: str, *, url: str) -> List[IDCandidate]:
        out: List[IDCandidate] = []
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        detail_context = _endpoint_has_domain(url, object_type)
        for idx, line in enumerate(lines):
            context = " ".join(lines[max(0, idx - 4): idx + 5])
            for m in KEY_VALUE_RE.finditer(line):
                key, value = m.group(1), m.group(2)
                key_c = _canon_key(key)
                if object_type == "partner" and any(x in key_c for x in ["system", "domain"]):
                    continue
                if object_type == "system" and "partner" in key_c:
                    continue
                if not self._looks_like_id(value):
                    continue
                source_type = "dom_detail" if detail_context else "text"
                score, reasons = _query_match_score(context, query, self.validator.aliases)
                out.append(IDCandidate(
                    target_type=object_type, candidate_id=value, candidate_name=None, source_type=source_type,
                    source_url=url, endpoint_url=url, field_path=key, surrounding_text=context[:1000],
                    matched_query=query if score >= 0.6 else None, confidence=0.10, evidence_reasons=reasons,
                    raw_excerpt={"line": line},
                ))
        return out

    def _from_tables(self, object_type: str, query: str, html: str, *, url: str) -> List[IDCandidate]:
        out: List[IDCandidate] = []
        soup = BeautifulSoup(html or "", "html.parser")
        qn = normalize(query)
        for table_idx, table in enumerate(soup.find_all(["table"])):
            headers = [self._clean(c.get_text(" ")) for c in table.find_all("th")]
            rows = []
            for tr in table.find_all("tr"):
                cells = [self._clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if not rows:
                continue
            if not headers:
                headers, data_rows = rows[0], rows[1:]
            else:
                data_rows = rows
            header_norm = [normalize(h) for h in headers]
            id_cols = [i for i, h in enumerate(header_norm) if "ID" in h or "IDENTIFIER" in h or "UUID" in h]
            name_cols = [i for i, h in enumerate(header_norm) if any(x in h for x in ["NAME", "PARTNER", "SYSTEM", "DOMAIN", "DESCRIPTION", "CODE"])]
            for row_idx, row in enumerate(data_rows):
                row_text = " | ".join(row)
                match_score, reasons = _query_match_score(row_text, query, self.validator.aliases)
                if qn and match_score < 0.60:
                    continue
                for col in id_cols:
                    if col < len(row):
                        val = row[col].strip()
                        if self._looks_like_id(val):
                            name = row[name_cols[0]] if name_cols and name_cols[0] < len(row) else None
                            out.append(IDCandidate(
                                target_type=object_type, candidate_id=val, candidate_name=name,
                                source_type="dom_table", source_url=url, endpoint_url=url,
                                field_path=headers[col] if col < len(headers) else f"column_{col}", surrounding_text=row_text,
                                matched_query=query if match_score >= 0.6 else None, confidence=0.15,
                                evidence_reasons=reasons + ["candidate appears in matching table row"],
                                raw_excerpt={"table_index": table_idx, "row_index": row_idx, "headers": headers, "row": row},
                            ))
        return out

    def _from_links_and_url(self, object_type: str, query: str, url: str, html: str) -> List[IDCandidate]:
        out: List[IDCandidate] = []
        urls = [(url, "current_url", "")]
        soup = BeautifulSoup(html or "", "html.parser")
        for a in soup.find_all("a"):
            href = a.get("href") or ""
            text = a.get_text(" ") or ""
            match_score, _ = _query_match_score(text + " " + href, query, self.validator.aliases)
            if href and (not query or match_score >= 0.6 or _endpoint_has_domain(href, object_type)):
                urls.append((href, "link", text))
        for u, source_label, link_text in urls:
            parsed = urlparse(u)
            params = parse_qs(parsed.query)
            for key, values in params.items():
                key_l = _canon_key(key)
                for val in values:
                    if not self._looks_like_id(val):
                        continue
                    out.append(IDCandidate(
                        target_type=object_type, candidate_id=val, candidate_name=None, source_type="url",
                        source_url=u, endpoint_url=u, field_path=key, surrounding_text=link_text or u,
                        matched_query=query if _query_match_score(link_text or u, query, self.validator.aliases)[0] >= 0.6 else None,
                        confidence=0.08, evidence_reasons=[f"ID-like value found in URL query param {key}"],
                        raw_excerpt={"source": source_label, "url": u},
                    ))
            bits = [b for b in parsed.path.split("/") if b]
            for i, bit in enumerate(bits):
                prev_next = " ".join(bits[max(0, i - 2): i + 3]).lower()
                if self._looks_like_id(bit) and (object_type in prev_next or i == len(bits) - 1):
                    out.append(IDCandidate(
                        target_type=object_type, candidate_id=bit, candidate_name=None, source_type="url",
                        source_url=u, endpoint_url=u, field_path="url_path", surrounding_text=link_text or u,
                        matched_query=query if _query_match_score(link_text or u, query, self.validator.aliases)[0] >= 0.6 else None,
                        confidence=0.06, evidence_reasons=["ID-like value found in URL path"], raw_excerpt={"source": source_label, "url": u},
                    ))
        return out

    def _dedupe_candidates(self, items: Iterable[IDCandidate]) -> List[IDCandidate]:
        best: Dict[Tuple[str, str, str, str], IDCandidate] = {}
        for item in items:
            key = (item.target_type, item.candidate_id, item.source_type, item.field_path or "")
            if key not in best or item.confidence > best[key].confidence:
                best[key] = item
        return sorted(best.values(), key=lambda x: x.confidence, reverse=True)

    def _dedupe(self, items: Iterable[ExtractedID]) -> List[ExtractedID]:
        best: Dict[Tuple[str, str, str], ExtractedID] = {}
        for item in items:
            key = (item.object_type, (item.query or "").upper(), item.object_id)
            if key not in best or item.confidence > best[key].confidence:
                best[key] = item
        return sorted(best.values(), key=lambda x: x.confidence, reverse=True)

    def _looks_like_id(self, value: str) -> bool:
        return self.validator._looks_like_id(value)

    def _clean(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()
