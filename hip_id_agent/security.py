from __future__ import annotations

import json
import re
from typing import Any, Iterable

# ``auth(?!or)`` keeps ``auth``/``auth_token``/``x-auth``/``oauth`` masked while
# leaving proof flags such as ``authoritative_execution_verified`` readable.
# Masking those flags turned ``True`` into ``"***MASKED***"`` and made every
# strict ``is True`` completion gate fail (Data Map stuck in retry_required).
SECRET_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|cookie|authorization|auth(?!or)|api[_-]?key|apikey|private[_-]?key|session|bearer|sftp[_-]?password|client[_-]?secret|certificate|as2[_-]?private[_-]?key|passphrase|set-cookie)",
    re.IGNORECASE,
)
SECRET_TARGET_RE = re.compile(
    r"(password|passwd|pwd|secret|token|apikey|api_key|client_secret|authorization|auth|cookie|private_key|private-key|certificate|sftp_password|as2_private_key|bearer)",
    re.IGNORECASE,
)
TOKEN_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+|([A-Za-z0-9._%+\-]+:[A-Za-z0-9._%+\-]+@)|((?:token|password|secret|api[_-]?key|authorization|cookie)\s*[=:]\s*)[^\s,;}]+",
)
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)


def mask_sensitive_string(value: str) -> str:
    if not value:
        return value
    masked = PRIVATE_KEY_RE.sub("***MASKED_PRIVATE_KEY***", value)
    masked = TOKEN_VALUE_RE.sub(lambda m: (m.group(1) or m.group(3) or "") + "***MASKED***", masked)
    masked = JWT_RE.sub("***MASKED_JWT***", masked)
    return masked


def mask_sensitive_data(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            # Booleans/None carry no secret material; keep flags such as
            # ``session_reused`` or ``token_present`` usable by gates.
            if SECRET_KEY_RE.search(str(k)) and not (v is None or isinstance(v, bool)):
                out[k] = "***MASKED***"
            else:
                out[k] = mask_sensitive_data(v)
        return out
    if isinstance(data, list):
        return [mask_sensitive_data(v) for v in data]
    if isinstance(data, tuple):
        return tuple(mask_sensitive_data(v) for v in data)
    if isinstance(data, str):
        return mask_sensitive_string(data)
    return data


def safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def compact_text(value: Any, max_len: int = 800) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = mask_sensitive_string(text)
    return text if len(text) <= max_len else text[:max_len] + "...<truncated>"


def is_secret_target(target: str | None) -> bool:
    return bool(SECRET_TARGET_RE.search(str(target or "")))
