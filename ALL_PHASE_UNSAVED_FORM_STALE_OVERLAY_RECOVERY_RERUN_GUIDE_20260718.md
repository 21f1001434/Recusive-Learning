# Rerun Guide

Use the same `run-full-dummy-fill` command.

Expected Data Map behavior:
1. Open listing.
2. Click Add once.
3. Verify Create Map surface.
4. Fill fields transactionally.
5. If a stale DDS loading veil persists, neutralize the veil in place without reloading.
6. Continue on the same Create Map form.
7. Never bind Table Search, Items per page or Page as Create Map fields.

Expected evidence:
- `data_map/mcp_runtime/loading_watchdog/stale_overlay_recovery_*.json`
- `status=stale_overlay_neutralized` when recovery is used.
- No `page_refresh_*.json` while an unsaved Create/Wizard surface is active.
