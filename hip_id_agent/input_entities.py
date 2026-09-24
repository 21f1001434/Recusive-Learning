from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _norm_key(k: str) -> str:
    return ''.join(ch for ch in str(k).lower() if ch.isalnum())


def extract_input_entities(path: str | Path) -> Dict[str, List[str]]:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    out: Dict[str, List[str]] = {
        'source_partner': [], 'target_partner': [], 'source_system': [], 'target_system': [],
        'partner': [], 'system': [], 'account': [], 'unknown': []
    }

    def add(bucket: str, value: Any) -> None:
        if value is None:
            return
        v = str(value).strip()
        if v and v.upper() not in {'TBD', 'NA', 'N/A', 'NULL'} and v not in out.setdefault(bucket, []):
            out[bucket].append(v)

    def walk(node: Any, path_parts: List[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                nk = _norm_key(k)
                role = 'unknown'
                joined = '.'.join(path_parts + [str(k)]).lower()
                if any(x in joined for x in ['sender', 'source']): role = 'source'
                if any(x in joined for x in ['receiver', 'target']): role = 'target'
                if isinstance(v, (dict, list)):
                    walk(v, path_parts + [str(k)])
                    continue
                if nk in {'partnername', 'partner', 'tradingpartnername'}:
                    add(f'{role}_partner' if role in {'source','target'} else 'partner', v)
                elif nk in {'systemname', 'system', 'domainname', 'domain'}:
                    add(f'{role}_system' if role in {'source','target'} else 'system', v)
                elif nk in {'accountname', 'account'}:
                    add('account', v)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, path_parts + [str(i)])
    walk(data, [])
    # De-duplicate overall partner/system buckets while preserving role-specific detail.
    for b in ['source_partner','target_partner']:
        for v in out[b]: add('partner', v)
    for b in ['source_system','target_system']:
        for v in out[b]: add('system', v)
    return out


def first_or_none(values: List[str]) -> Optional[str]:
    return values[0] if values else None
