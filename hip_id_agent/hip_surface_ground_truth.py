"""Stable HIP UI facts learned from supervised Dell portal evidence.

These are *semantic signatures*, never generated Angular ids or screen coordinates.
They seed live discovery/AutoWebGLM with facts that were directly witnessed in the
portal and are still revalidated against the current DOM before every action.
"""
from __future__ import annotations

DATA_MAP_LISTING_SIGNATURE = {
    "route_suffix": "/hybrid-integrations/securelink/datamaps",
    "page_add": {
        "role": "button",
        "accessible_name": "Add",
        "dds_host": "dds-button",
        "dds_kind": "tertiary",
        "dds_size": "sm",
        "region_hints": ["dds-table__ribbon__action-bar", "dds-table-bulk-actions-bar", "table"],
        "position": "upper-right",
    },
    "negative_actions": [
        "Expand the row", "Filter", "Manage Columns", "Edit", "Clone", "Migrate",
        "Cancel", "Submit",
    ],
}

DATA_MAP_CREATE_SIGNATURE = {
    "same_route": True,
    "surface": ["app-generic-drawer", "dds-drawer", "[role=dialog][aria-modal=true]"],
    "title": "Create Map",
    "required_field_names": ["Map Identifier", "Map Name", "Map Class", "Contivo version", "Map Data"],
    "other_controls": ["Status", "Cross Reference Table details", "Input Schema Validation", "Output Schema Validation"],
    "terminal_actions": ["Cancel", "Submit"],
    "negative_existing_row_markers": ["DEV", "TEST1", "TEST2", "PROD", "Edit", "Clone", "Migrate"],
}

# This profile deliberately contains no customer values and no transient ids.
SITE_GROUND_TRUTH_VERSION = "2026-09-10.supervised-datamap-v1"
