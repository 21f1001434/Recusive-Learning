from __future__ import annotations

import re
from typing import Any

SFTP_HAFT_SENDER_DEPLOYMENT_GROUP = "da-sender-sftphaft-dce-shared"
SFTP_HAFT_RECEIVER_DEPLOYMENT_GROUP = "pt-receiver-sftphaft-dce-shared"

_LEGACY_SENDER = {
    "dce-shared-sender",
    "dce-default-sender",
    "sender",
}
_LEGACY_RECEIVER = {
    "dce-shared-receiver",
    "dce-default-receiver",
    "receiver",
}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def is_sftp_haft(interface_type: Any) -> bool:
    value = _norm(interface_type)
    return value in {"sftphaft", "sftp", "haftsftp"} or ("sftp" in value and "haft" in value)


def normalize_profile_usage(value: Any) -> str:
    text = str(value or "").strip().lower()
    if any(x in text for x in ("sender", "source", "dell")):
        return "sender"
    if any(x in text for x in ("receiver", "target", "partner")):
        return "receiver"
    return text


def resolve_transport_deployment_group(
    *,
    interface_type: Any,
    profile_usage: Any,
    current_value: Any = None,
) -> str:
    """Return the role-aware SFTP-HAFT deployment group when the current value is blank/legacy.

    Explicit non-legacy deployment groups remain untouched so a live portal/customer
    configuration can override the default. This policy only applies to SFTP-HAFT.
    """
    current = str(current_value or "").strip()
    if not is_sftp_haft(interface_type):
        return current
    role = normalize_profile_usage(profile_usage)
    current_n = current.lower()
    if role == "sender":
        if not current or current_n in _LEGACY_SENDER:
            return SFTP_HAFT_SENDER_DEPLOYMENT_GROUP
        return current
    if role == "receiver":
        if not current or current_n in _LEGACY_RECEIVER:
            return SFTP_HAFT_RECEIVER_DEPLOYMENT_GROUP
        return current
    return current
