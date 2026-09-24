from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Literal

import yaml
from pydantic import BaseModel, Field


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(os.path.expanduser(value))
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


class PortalConfig(BaseModel):
    # Use the Dell BizLink pages directly by default. The user/browser session must have Dell SSO access.
    base_url: str = "https://developer.dell.com/hybrid-integrations/bizlink/partner"
    environment: str = "DEV"
    browser_user_data_dir: str = "./data/chrome_profile"
    headless: bool = False
    timeout_ms: int = 45000
    sso_timeout_seconds: int = 900
    # Portal loading watchdog. The agent waits for genuine Angular/DDS loading
    # to finish. When the same blocking loading state persists for five minutes,
    # it refreshes the current page once inside the same authenticated context.
    loading_watchdog_timeout_seconds: int = 300
    loading_watchdog_poll_seconds: float = 1.0
    loading_watchdog_max_refreshes_per_phase: int = 1
    # A generic spinner/progressbar/aria-busy marker is not enough to block the
    # agent. The watchdog requires consecutive evidence that a visible overlay
    # actually covers the active form or intercepts the requested control.
    loading_watchdog_consecutive_blocking_samples: int = 2
    loading_watchdog_min_viewport_ratio: float = 0.08
    loading_watchdog_min_surface_cover_ratio: float = 0.45
    loading_watchdog_ignore_passive_indicators: bool = True
    # A page reload is allowed only after the blocking loader has remained continuously
    # present for the timeout AND the configured multimodal vision model confirms
    # that a real loading surface is still visible.
    loading_watchdog_vision_confirm_before_refresh: bool = True
    loading_watchdog_vision_confidence_threshold: float = 0.70
    loading_watchdog_vision_fail_closed: bool = True
    autonomous_page_health_enabled: bool = True
    chromium_channel: str | None = "chrome"  # Google Chrome Stable is the primary HIP browser; Edge is startup fallback.
    edge_executable_path: str | None = None  # optional full path to msedge.exe for locked-down corporate images
    chrome_executable_path: str | None = None  # optional explicit Google Chrome path
    # Browser fallback is startup-only.  Once Dell SSO/mission execution begins,
    # the browser is never switched underneath the authenticated task.
    allow_browser_fallback: bool = True
    fallback_to_chrome: bool = True
    fallback_to_edge: bool = True
    fallback_to_playwright_chromium: bool = True
    chrome_user_data_dir: str = "./data/chrome_profile"
    edge_user_data_dir: str = "./data/edge_profile"
    chromium_user_data_dir: str = "./data/chromium_profile"
    startup_cdp_probe_seconds: float = 8.0
    slow_mo_ms: int = 0
    launch_args: List[str] = Field(default_factory=lambda: ["--start-maximized", "--disable-blink-features=AutomationControlled"])
    sso_success_url_patterns: List[str] = Field(default_factory=lambda: ["/hybrid-integrations", "/bizlink", "/portal", "/configuration", "/home", "/dashboard"])
    sso_login_url_keywords: List[str] = Field(default_factory=lambda: ["login", "signin", "sso", "auth", "saml", "oauth", "okta", "ping"])
    sso_positive_texts: List[str] = Field(default_factory=lambda: ["partner", "system", "bizlink", "hybrid integrations", "configuration", "transport profile", "flow"])




class BrowserConfig(BaseModel):
    search_timeout_ms: int = 30000
    detail_timeout_ms: int = 30000
    network_idle_timeout_ms: int = 5000


class NavigationConfig(BaseModel):
    # These are the first-class routes requested by the user.
    partner_candidate_paths: List[str] = Field(default_factory=lambda: [
        "https://developer.dell.com/hybrid-integrations/bizlink/partner",
        "/hybrid-integrations/bizlink/partner",
    ])
    system_candidate_paths: List[str] = Field(default_factory=lambda: [
        "https://developer.dell.com/hybrid-integrations/bizlink/system",
        "/hybrid-integrations/bizlink/system",
    ])
    partner_nav_texts: List[str] = Field(default_factory=lambda: ["Partners", "Partner", "Trading Partners", "Partner Management", "BizLink Partner"])
    system_nav_texts: List[str] = Field(default_factory=lambda: ["Systems", "System", "System Management", "BizLink System"])
    search_labels: List[str] = Field(default_factory=lambda: ["Search", "Filter", "Name", "Partner Name", "System Name", "Keyword"])
    detail_link_texts: List[str] = Field(default_factory=lambda: ["View", "Details", "Edit", "Open", "Manage", "Name", "Identifier"])
    menu_button_texts: List[str] = Field(default_factory=lambda: ["Menu", "Navigation", "Configuration", "Setup", "Admin", "Hybrid Integrations", "BizLink"])


class ExtractionConfig(BaseModel):
    minimum_confidence: float = 0.90
    open_first_matching_row: bool = True
    capture_network_json: bool = True
    capture_all_network_metadata: bool = True
    capture_cdp_network: bool = True
    save_har: bool = False
    max_network_body_chars: int = 150000
    max_network_events_in_report: int = 120
    query_aliases: List[str] = Field(default_factory=list)
    include_previous_network_memory: bool = True
    collect_dom_clicks: bool = True
    collect_dom_events: bool = True
    collect_dom_mutations: bool = True
    max_dom_event_records: int = 6000
    max_dom_mutation_records: int = 6000
    screenshot_each_click: bool = False
    max_explore_clicks: int = 12
    overwrite_existing_ids: bool = False


class ExplorationConfig(BaseModel):
    enabled: bool = False
    collect_all_pages: bool = False
    explore_partner: bool = True
    explore_system: bool = True
    # BizLink Partner/System pages are parent-card first. Child items such as
    # AS2TEST partners and AIC-DCE domains often do not appear from the top
    # grid search; they appear only after opening an Account/Domain parent card
    # action menu (Show Partner(s) / View Domain). When exploration is enabled,
    # prefer that parent-card discovery before direct child search.
    prefer_nested_discovery: bool = True
    # In Dell BizLink the query AS2TEST/AIC-DCE is usually a child row under
    # a parent Account card. Do not search the child query in the top-level
    # Account/System grid by default during exploration.
    search_first: bool = False
    # If nested discovery cannot find the child, keep this false to avoid
    # typing a child partner/domain into the wrong top-level parent search box.
    # Set true only for portals where the child is directly searchable.
    allow_direct_child_search_fallback: bool = False
    max_pages: int = 5
    max_cards_per_page: int = 80
    max_actions_per_entity: int = 6
    max_total_actions: int = 80
    allow_unsafe_clicks: bool = False
    open_edit_pages_for_readonly_capture: bool = True

    # Dedicated HIP form exploration agent. It enumerates structural parent values,
    # records the child controls revealed/hidden/enabled for each value, and persists
    # a plan-ready form knowledge graph. Final mutations remain blocked.
    form_knowledge_agent_enabled: bool = True
    explore_parent_value_branches: bool = True
    max_parent_controls: int = 24
    max_values_per_parent: int = 30
    settle_ms: int = 700
    use_llm_dependency_analyst: bool = True
    fail_closed_on_restore_error: bool = False
    safe_action_keywords: List[str] = Field(default_factory=lambda: ["view", "show", "details", "detail", "open", "edit", "partners", "domains", "systems", "users", "biz flows", "deployment groups"])
    unsafe_action_keywords: List[str] = Field(default_factory=lambda: ["delete", "remove", "update", "save", "submit", "create", "add", "reset", "disable", "enable", "archive"])




class PortalBrainConfig(BaseModel):
    # Persistent long-term memory shared by every HIP Portal run. Knowledge is
    # promoted only after deterministic/LLM/vision judge approval.
    enabled: bool = True
    directory: str = "portal_brain"
    bootstrap_from_runs: bool = True
    require_judge_pass_for_promotion: bool = True
    learn_warning_runs_as_candidates: bool = True
    min_validated_edge_confidence: float = 0.60
    max_source_runs: int = 300
    prefer_semantic_locators: bool = True
    reject_dynamic_selectors_as_primary: bool = True

    # Reviewed canonical HIP KB. It seeds page identity, field/input mappings,
    # parent-value-child dependencies, hard gates and negative evidence. Canonical
    # knowledge guides planning but never replaces live MCP/DOM/text/vision proof.
    auto_import_unified_kb: bool = True
    unified_kb_path: str = "./knowledge_base/HIP_Unified_Deep_KB.json"
    unified_kg_path: str = "./knowledge_base/HIP_Unified_Knowledge_Graph.json"
    unified_kb_required: bool = True
    force_unified_kb_reimport: bool = False

    # Adaptive KB self-healing. The reviewed KB is a strong seed, but live
    # Playwright-MCP exploration plus deterministic/text/vision judge evidence
    # may add missing facts or supersede a proven-wrong canonical dependency.
    self_heal_kb: bool = True
    auto_apply_validated_kb_repairs: bool = True
    kb_repair_min_confirmations: int = 1
    kb_supersede_min_confirmations: int = 2
    preserve_original_kb: bool = True
    export_corrected_kb_after_run: bool = True
    revalidate_known_parent_branches: bool = True

    # Reusable structural memory for similar HIP flow/form families. Values are
    # never stored; only judge-validated topology, semantic bindings, action order
    # and wait/event contracts are eligible for replay.
    flow_pattern_memory_enabled: bool = True
    flow_pattern_min_similarity: float = 0.78
    flow_pattern_max_patterns: int = 500
    flow_pattern_allow_cross_phase_same_family: bool = True

    # V233 Stage 3: persistent value-free semantic website world model.
    # It learns only judge/effect-verified states, controls and transitions and
    # reuses them as bounded priors. Live website evidence always remains authoritative.
    world_model_enabled: bool = True
    world_model_min_validated_confirmations: int = 1
    world_model_min_validated_confidence: float = 0.72
    world_model_failure_decay: float = 0.18
    world_model_use_for_candidate_scoring: bool = True
    world_model_use_for_planning: bool = True
    # V234 adaptive-memory health. Historical success is useful as a prior, but
    # confidence decays with age and repeated contradictions are treated as portal
    # drift. Live DOM/accessibility evidence always remains authoritative.
    world_model_recency_decay_enabled: bool = True
    world_model_confidence_half_life_days: float = 45.0
    world_model_stale_after_days: float = 90.0
    world_model_drift_failure_threshold: int = 2
    world_model_drift_penalty: float = 0.22
    world_model_auto_demote_on_drift: bool = True

class PortalLearningConfig(BaseModel):
    # Deep portal-learning layer. It observes every phase through Python
    # Playwright, Playwright MCP and Chrome DevTools MCP, then promotes only
    # judge-approved facts into the persistent Portal Brain.
    enabled: bool = True
    capture_accessibility_snapshots: bool = True
    accessibility_snapshot_depth: int = 12
    capture_devtools_snapshots: bool = True
    capture_api_contracts: bool = True
    capture_validation_rules: bool = True
    capture_storage_key_names: bool = True
    capture_performance_trace: bool = True
    # Performance traces are intentionally limited to retries by default because
    # tracing every successful action adds cost without improving form learning.
    performance_trace_on_retry_only: bool = True
    max_network_contracts_per_phase: int = 250
    max_control_signatures_per_phase: int = 800
    max_exploration_agenda_items: int = 50
    promote_only_after_judge_pass: bool = True
    detect_portal_drift: bool = True
    information_gain_agenda: bool = True
    fail_open_for_learning_capture: bool = True
    # Maximum-observability mode captures structural DOM, hidden/visible control
    # ownership, mutation/event timelines, resources and input-to-control coverage.
    # It is read-only and strips values/credentials before writing evidence.
    maximum_observability_enabled: bool = True
    capture_sanitized_dom_structure: bool = True
    maximum_observability_max_controls: int = 5000
    maximum_observability_max_events: int = 2500
    maximum_observability_max_dom_chars: int = 2000000
    # V233 Stage 1: build a read-only current-generation world model before actions.
    # This captures foreground surface/layout, semantic controls, owned DDS popups,
    # mounted options, tabs/accordions/repeatable regions and recent events/mutations.
    deep_website_understanding_enabled: bool = True
    website_understanding_max_controls: int = 3000
    website_understanding_max_options_per_control: int = 500
    website_understanding_max_events: int = 500
    capture_registered_event_listener_types: bool = True
    website_understanding_event_listener_control_limit: int = 160
    website_understanding_min_confidence: float = 0.72
    # V233 Stage 2: Browser-Use-style human live view. This stores only concrete
    # browser evidence and masked values, never hidden model chain-of-thought.
    agent_live_view_enabled: bool = True
    agent_live_view_capture_screenshots: bool = True
    agent_live_view_capture_website_summary: bool = True
    agent_live_view_history_limit: int = 120
    # V234 Browser-Use-style multi-colour visual intelligence overlay. Geometry is
    # computed transiently in the live page and is never persisted into memory/replay.
    visual_overlay_enabled: bool = True
    visual_overlay_max_seen_controls: int = 80
    visual_overlay_max_candidates: int = 8
    visual_overlay_show_labels: bool = True
    visual_overlay_highlight_foreground_surface: bool = True
    visual_overlay_result_hold_ms: int = 1400




class AutonomousFormConfig(BaseModel):
    """Goal-driven live form execution independent of fixed selectors/coordinates.

    V231 promotes the autonomous goal runtime from Data Map-only to every
    form phase.  Phase-specific knowledge may prepare rows/tabs/parents, but
    no phase is complete until the shared live goal runtime proves the target.
    """
    enabled: bool = True
    apply_to_data_map: bool = True
    apply_to_document_types: bool = True
    apply_to_rules: bool = True
    apply_to_transport_profiles: bool = True
    apply_to_biz_flow: bool = True
    apply_to_all_form_phases: bool = True
    max_adaptive_cycles: int = 5
    no_progress_cycle_limit: int = 2
    use_autowebglm_live_observation: bool = True
    use_dell_aia_binding_advisor_on_ambiguity: bool = True
    rediscover_controls_before_every_action: bool = True
    require_exact_readback: bool = True
    require_authoritative_executor_proof: bool = True
    persist_only_judge_verified_semantics: bool = True
    never_persist_selectors_or_coordinates: bool = True
    # Run-local replay artifacts remain useful, but adaptive missions store only
    # semantic identities/labels and remove transient selectors/coordinates.
    semantic_replay_blueprint_only: bool = True
    # V233 Stage 4: make the verified semantic world model operational. Memory may
    # propose the next transition/portal-owned dropdown branch, but live DOM proof
    # and normal mutation governance remain mandatory before execution.
    operational_world_model_enabled: bool = True
    dynamic_option_resolution_enabled: bool = True
    allow_unique_validated_memory_option_when_input_missing: bool = True
    transition_planning_enabled: bool = True
    transition_plan_requires_live_reproof: bool = True
    mutation_governance_always_required: bool = True

class RuntimeSelfHealConfig(BaseModel):
    # Closed-loop runtime recovery. Repairs are safe browser/session operations
    # only; final portal mutations remain prohibited.
    enabled: bool = True
    max_phase_attempts: int = 5
    max_total_repairs: int = 20
    max_repeated_failure_signature: int = 2
    # Anti-stagnation guards are NEVER disabled by ``until_complete``. A live
    # portal phase must demonstrate progress; the same failure state cannot be
    # replayed forever and no phase may occupy the browser for hours.
    max_no_progress_repeats: int = 3
    max_phase_wall_seconds: int = 1200
    # Active structural watchdog. Unlike failure-signature counting, this runs
    # while the phase coroutine is still alive and interrupts repeated UI cycles.
    no_progress_watchdog_seconds: float = 90.0
    no_progress_poll_seconds: float = 5.0
    no_progress_recent_signature_limit: int = 12
    use_aia_advisor: bool = True
    aia_advisor_timeout_seconds: int = 20
    aia_advisor_context_max_chars: int = 96000
    capture_evidence: bool = True
    retry_unknown_once: bool = True
    fail_closed: bool = True
    # When enabled, ordinary attempt count is progress-driven rather than fixed.
    # Anti-stagnation signature and wall-clock guards remain mandatory so a
    # broken Document Type/DDS control can never spin for hours.
    until_complete: bool = False
    exploration_exploitation: bool = True
    # Capture independent evidence from local Playwright plus both browser MCPs
    # for every failed attempt. This is intentionally structured/redacted rather
    # than an unrestricted page dump, so it remains safe to upload and review.
    forensic_evidence: bool = True
    forensic_event_window: int = 250
    capture_validated_trajectory: bool = True


class APIRequestConfig(BaseModel):
    name: str
    method: str = "POST"
    path: str
    body_path: str | None = None


class APIConfig(BaseModel):
    # Legacy configured API client. Real writes remain disabled by default.
    enabled: bool = False
    dry_run: bool = True
    base_url: str = ""
    token_env_var: str = "HIP_API_TOKEN"
    token_command: List[str] = Field(default_factory=list)
    requester_id: str = ""
    account_id: str = ""
    work_order_id: str = ""
    requests: List[APIRequestConfig] = Field(default_factory=list)

    # Browser-grounded form/API intelligence. The same authenticated Chrome
    # session captures every request caused by form open/fill. No endpoint or
    # payload key may be invented by the LLM.
    capture_observed_contracts: bool = True
    dual_ui_api: bool = False
    capture_submit_payload: bool = False
    execution_mode: Literal["capture", "dry_run", "validate", "write"] = "capture"
    require_capture_for_completion: bool = False
    max_observed_contracts_per_phase: int = 500
    capture_request_response_transactions: bool = True
    max_observed_transactions_per_phase: int = 2000
    export_openapi: bool = True
    export_postman: bool = True
    build_ui_api_crosswalk: bool = True
    replay_observed_safe_reads: bool = False
    replay_observed_validation_requests: bool = False

    # Write mode is two-key fail-closed: the CLI/config flag and the environment
    # variable must both be enabled. The captured submit request is blocked first;
    # only the separately authenticated API replay may perform a mutation.
    allow_mutating_methods: bool = False
    mutation_confirmation_env_var: str = "HIP_ALLOW_API_MUTATION"
    allowed_api_hosts: List[str] = Field(default_factory=list)


class AIAConfig(BaseModel):
    enabled: bool = False
    endpoint_env_var: str = "AIA_ENDPOINT"
    base_url_env_var: str = "OPENAI_BASE_URL"
    token_env_var: str = "AIA_TOKEN"
    token_command: List[str] = Field(default_factory=list)
    auth_mode: Literal["auto", "sso", "client_credentials", "env", "command"] = "auto"
    client_id_env_var: str = "CLIENT_ID"
    client_secret_env_var: str = "CLIENT_SECRET"
    use_aia_auth_package: bool = True
    model: str = "gpt-oss-120b"
    timeout_seconds: int = 90
    max_snapshot_chars: int = 40000


class ReportingConfig(BaseModel):
    runs_dir: str = "./runs"
    memory_dir: str = "./data/hip_memory"
    screenshot_dir_name: str = "screenshots"
    save_html: bool = True
    save_markdown: bool = True
    save_json: bool = True


class SecurityConfig(BaseModel):
    mask_secrets: bool = True
    save_cookies: bool = False
    save_tokens: bool = False
    save_authorization_headers: bool = False


class GovernanceConfig(BaseModel):
    # Production change-governance controls layered on top of the existing
    # three-key mutation gate. Read/draft navigation remains lightweight;
    # mutation operations additionally require an authorized operator role,
    # duplicate protection and post-change verification.
    enabled: bool = True
    operator_role_env_var: str = "HIP_OPERATOR_ROLE"
    default_operator_role: str = "viewer"
    approval_id_env_var: str = "HIP_CHANGE_APPROVAL_ID"
    require_approval_id_for_mutation: bool = False
    read_roles: List[str] = Field(default_factory=lambda: ["viewer", "business", "support", "technical", "admin"])
    draft_roles: List[str] = Field(default_factory=lambda: ["business", "support", "technical", "admin"])
    mutation_roles: List[str] = Field(default_factory=lambda: ["technical", "admin"])
    require_certified_family_for_mutation: bool = True
    require_preview_for_mutation: bool = True
    require_post_change_api_success: bool = True
    require_post_change_mcp_assurance: bool = True
    duplicate_window_hours: int = 168
    # After any physical mutation click dispatch, never fire a second backend
    # mutation merely because the browser/UI timed out. Reconcile read-only
    # network and visible outcome evidence inside this bounded window instead.
    mutation_reconciliation_timeout_seconds: float = 3.0
    mutation_reconciliation_poll_seconds: float = 0.25
    allow_pre_dispatch_rebind: bool = True
    # An identical mutation whose prior process ended after execution started or
    # with an indeterminate/partial write outcome is quarantined across runs.
    # force_repeat_mutation does not bypass this fail-closed safety boundary.
    block_unresolved_mutation_replay: bool = True
    ledger_filename: str = "change_audit_ledger.jsonl"


class MCPConfig(BaseModel):
    # Three complementary MCPs: Playwright and Chrome DevTools attach to the
    # authenticated browser; HIP Intelligence is a local value-free planner,
    # critic, drift and trajectory-memory service and opens no browser.
    browser_backend: Literal["playwright", "mcp", "auto"] = "mcp"
    use_playwright_mcp: bool = True
    use_chrome_devtools_mcp: bool = True
    use_browser_mcp_for_sso: bool = False

    # Local value-free intelligence MCP. It does not control another browser.
    # It exposes web representation, action planning, critic, drift and
    # trajectory-memory tools used by the shared HIP state-transaction engine.
    use_hip_intelligence_mcp: bool = True
    hip_intelligence_mcp_required: bool = True
    hip_intelligence_mcp_command: str = ""  # empty = current Python executable
    hip_intelligence_mcp_args: List[str] = Field(default_factory=list)
    hip_intelligence_mcp_startup_timeout_seconds: int = 20
    # V232: normal autonomous execution may use whichever execution/evidence channels
    # are healthy.  The explicit --require-mcp profile flips this to True and
    # requires the full Playwright + DevTools + HIP Intelligence MCP stack.
    strict_runtime_required: bool = True

    # Official Microsoft Playwright MCP server. It attaches to the same Chrome/Edge
    # instance through CDP so accessibility snapshots, safe actions and verification
    # operate on the exact HIP Portal tab used by the deterministic executor.
    playwright_mcp_command: str = "auto"
    playwright_mcp_args: List[str] = Field(default_factory=lambda: ["@playwright/mcp@0.0.79"])
    playwright_mcp_startup_timeout_seconds: int = 45
    playwright_mcp_remote_debugging_port: int = 9237
    playwright_mcp_cdp_timeout_ms: int = 30000
    playwright_mcp_action_timeout_ms: int = 15000
    playwright_mcp_navigation_timeout_ms: int = 60000
    playwright_mcp_expect_timeout_ms: int = 10000
    playwright_mcp_preflight_headless: bool = True
    playwright_mcp_primary_for_safe_actions: bool = True
    playwright_mcp_find_first: bool = True
    playwright_mcp_fill_form_enabled: bool = True
    playwright_mcp_fill_form_min_fields: int = 2
    playwright_mcp_verify_every_action: bool = True
    playwright_mcp_snapshot_after_action: bool = True
    playwright_mcp_required_when_require_mcp: bool = True

    # Pre-existing Chrome DevTools MCP stdio server. Used alongside Playwright MCP
    # as an independent CDP/DOM/network/console witness in governed Layer-11 execution.
    chrome_devtools_command: str = "auto"
    chrome_devtools_args: List[str] = Field(default_factory=lambda: ["chrome-devtools-mcp@1.6.0", "--no-usage-statistics"])
    chrome_devtools_startup_timeout_seconds: int = 30
    chrome_devtools_request_timeout_seconds: float = 30.0
    chrome_devtools_shutdown_timeout_seconds: float = 3.0
    chrome_devtools_mcp_direct_backend_enabled: bool = False
    chrome_devtools_attach_same_browser: bool = True
    chrome_devtools_experimental_page_id_routing: bool = True
    require_dual_mcp_same_surface: bool = True
    # SSO and Angular redirects can temporarily hide the selected page from the
    # Chrome DevTools MCP. Poll after authentication rather than failing on the
    # first empty list_pages response.
    dual_mcp_same_surface_timeout_seconds: float = 30.0
    dual_mcp_same_surface_poll_seconds: float = 1.0

    # v2.1 Layer 6: same-browser executor recovery. If an MCP/CDP transport
    # disconnects after SSO, only the MCP clients may be restarted; the browser
    # process/profile is locked. The exact HIP tab/route must be uniquely re-proven
    # before execution continues.
    executor_rebind_enabled: bool = True
    executor_rebind_timeout_seconds: float = 12.0
    executor_rebind_max_attempts_per_phase: int = 2
    executor_rebind_fail_on_ambiguous_tabs: bool = True


class ExpertSkillConfig(BaseModel):
    """Description-triggered deterministic skill orchestration.

    These controls encode the operating principles used by the HIP agent: a user
    description selects vetted expert skills, deterministic scripts execute first,
    and only phase-local context is loaded unless recovery needs more evidence.
    """
    enabled: bool = True
    description_trigger_enabled: bool = True
    require_skill_vetting: bool = True
    deterministic_first: bool = True
    # Larger but still bounded context. Normal planning is phase-local; recovery
    # may expand into broader semantic evidence without dumping cookies/values.
    context_max_chars: int = 64000
    recovery_context_max_chars: int = 128000
    max_capabilities_per_family: int = 48
    browser_use_recovery_context: bool = True
    broad_context_only_on_recovery: bool = True


class AutoWebGLMConfig(BaseModel):
    """Primary AutoWebGLM browser-decision framework for HIP.

    AutoWebGLM owns the observation/action policy layer: task + simplified HTML +
    viewport + action history -> one browser command.  Existing HIP deterministic
    drivers are registered as verified tool adapters that execute the approved
    action and prove the resulting Angular/DDS state.  The original ChatGLM3-6B
    research checkpoint is optional; the existing Dell AIA endpoint can predict
    the same protocol when no native wrapper is configured.
    """
    enabled: bool = True
    primary_framework: bool = True
    recovery_only: bool = False
    deterministic_tool_fallback: bool = True
    require_intent_alignment: bool = True
    max_primary_decision_seconds: int = 12
    use_existing_dell_aia: bool = True
    native_model_command: List[str] = Field(default_factory=list)
    max_html_chars: int = 70000
    max_history_actions: int = 40
    max_tabs: int = 12
    max_prompt_chars: int = 110000
    timeout_seconds: int = 20
    require_agentq_reward_gate: bool = True
    allowed_actions: List[str] = Field(default_factory=lambda: [
        "click", "hover", "select", "type_string", "press_key", "scroll_page", "go",
        "jump_to", "switch_tab", "user_input", "finish"
    ])
    block_mutation_labels: bool = True




class SemanticUnderstandingConfig(BaseModel):
    """Layer 11 semantic website-understanding and multi-evidence action gate.

    The gate never invents business values. It proves that a reviewed deterministic
    intent still resolves to one live HIP control before Playwright MCP can execute
    it, and learns only value-free control/state fingerprints after exact success.
    """
    enabled: bool = True
    execute_confidence_threshold: float = 0.90
    # Exact reviewed Playwright locators are stronger evidence than a free-form
    # semantic search.  Safe, non-committing navigation/structural actions use a
    # lower anchored threshold while still requiring a unique live locator.
    anchored_execute_confidence_threshold: float = 0.64
    structural_opener_confidence_threshold: float = 0.52
    anchored_ambiguity_margin: float = 0.02
    prefer_vetted_locator_anchor: bool = True
    reobserve_confidence_threshold: float = 0.75
    self_heal_confidence_threshold: float = 0.55
    ambiguity_margin: float = 0.08
    require_playwright_mcp_evidence: bool = True
    require_devtools_evidence: bool = True
    use_hip_intelligence_mcp_consensus: bool = True
    require_hip_intelligence_mcp_evidence: bool = True
    # In adaptive mode an unavailable external witness is advisory rather than a
    # hard blocker.  Explicit --require-mcp enables strict_external_evidence.
    strict_external_evidence: bool = True
    use_vision_for_ambiguity: bool = True
    vision_confirmation_boost: float = 0.08
    fail_closed: bool = True
    require_post_action_effect: bool = True
    revalidate_before_dispatch: bool = True
    prefer_stable_semantic_selector_for_mcp: bool = True
    learn_control_fingerprints: bool = True
    learn_state_graph: bool = True
    max_ranked_candidates_evidence: int = 6

class LangChainBrowserToolkitConfig(BaseModel):
    """Read-only LangChain Playwright toolkit on the SAME authenticated Edge.

    CDP attachment is recovery-only by default.  The primary Playwright context owns
    Edge during SSO and normal deterministic filling; auxiliary CDP clients are not
    allowed to destabilize the login/session path.
    """
    enabled: bool = True
    attach_same_browser: bool = True
    startup_attach: bool = False
    attach_timeout_seconds: float = 8.0
    read_only: bool = True
    fail_open_if_unavailable: bool = True
    timeout_seconds: float = 6.0
    max_text_chars: int = 30000
    max_element_chars: int = 30000
    allowed_tools: List[str] = Field(default_factory=lambda: [
        "current_webpage", "extract_text", "get_elements", "extract_hyperlinks"
    ])


class PyAutoGUIConfig(BaseModel):
    """Primary visible-desktop interaction engine for the HIP browser.

    The semantic/DOM stack still proves *what* control is intended, but the first
    physical interaction is performed through the audited PyAutoGUI MCP channel on
    Windows. Playwright MCP remains attached to the same browser for semantic
    discovery, exact-value/effect verification, and deterministic fallback.
    """
    enabled: bool = True
    windows_only: bool = True

    # Primary interaction policy.  In the real Windows runtime the shipped config
    # sets mcp_required=true, so a missing desktop MCP fails readiness instead of
    # silently pretending the primary executor exists.
    interaction_mode: Literal["primary", "fallback"] = "primary"
    primary_for_clicks: bool = True
    primary_for_form_fill: bool = True
    primary_for_search_fill: bool = True
    primary_for_keys: bool = True
    fallback_to_playwright_mcp: bool = True
    fallback_to_python_playwright: bool = True
    require_semantic_target_for_web_actions: bool = True

    # Community pyautogui-mcp package, run from the current Python environment by
    # default: ``python -m pyautogui_mcp --transport stdio --prefix pyautogui_``.
    mcp_enabled: bool = True
    mcp_required: bool = False
    mcp_command: str = ""
    mcp_args: List[str] = Field(default_factory=list)
    mcp_prefix: str = "pyautogui_"
    mcp_startup_timeout_seconds: int = 20
    mcp_request_timeout_seconds: float = 15.0
    mcp_shutdown_timeout_seconds: float = 3.0
    mcp_stream_limit_bytes: int = 32 * 1024 * 1024
    prefer_mcp: bool = True

    # Backward-compatible recovery flags retained for older call sites. They are
    # also used when interaction_mode=fallback.
    prefer_mcp_before_local_web_fallback: bool = True
    use_for_structural_web_recovery: bool = True
    use_for_form_fill_recovery: bool = True
    use_for_key_recovery: bool = True
    allow_local_fallback_if_mcp_unavailable: bool = True

    # Mutation clicks are allowed only after BrowserSession's existing mutation
    # authorization + semantic revalidation + quarantine guard.  This is required
    # for true end-to-end PyAutoGUI-primary execution; it is not an ungoverned
    # coordinate click bypass.
    allow_mutation_clicks: bool = True
    allow_native_screen_targets: bool = True
    native_target_confidence_threshold: float = 0.97
    native_target_evidence_sources: List[str] = Field(default_factory=lambda: ["vision", "native_dialog", "browser_chrome"])
    ascii_only_text: bool = True
    max_attempts_per_action: int = 2
    locator_timeout_ms: int = 5000
    stability_wait_ms: int = 150
    bbox_stability_tolerance_px: float = 3.0
    screen_margin_px: int = 2
    move_duration_seconds: float = 0.12
    key_interval_seconds: float = 0.01
    settle_ms: int = 300
    pause_seconds: float = 0.05
    verify_after_fill: bool = True
    verify_after_click_event: bool = True

    # Visual structural-opener recovery is used only when DOM/ARIA discovery cannot
    # produce a locator. Gemma/vision returns a normalized viewport target; the
    # click is still performed through PyAutoGUI MCP and the expected in-page state
    # must be observed immediately afterwards.
    visual_structural_recovery_enabled: bool = True
    visual_target_confidence_threshold: float = 0.94
    visual_target_max_attempts: int = 2



class VisionRuntimeConfig(BaseModel):
    """Multimodal vision perception used during normal recovery and loading health.

    The existing strict SectionJudge remains the final vision gate. This bridge gives
    the live browser controller visual awareness before section completion, including
    the five-minute loading watchdog requested for Edge.
    """
    enabled: bool = True
    require_model: bool = True
    use_for_recovery: bool = True
    use_for_loading_watchdog: bool = True
    loading_refresh_after_seconds: int = 300
    loading_confidence_threshold: float = 0.70
    timeout_seconds: int = 30
    max_prompt_chars: int = 24000
    max_recovery_summary_chars: int = 12000
    fail_closed_when_unavailable: bool = True
    auto_discovery: bool = True
    auto_candidates: List[str] = Field(default_factory=lambda: ["gemma-3-27b-it", "pixtral-12b-2409", "florence-2-large-ft"])

class BrowserUseConfig(BaseModel):
    """Browser-Use/WebUI-inspired browser/session capabilities for HIP.

    Browser Use remains perception/recovery only. PyAutoGUI MCP is the primary
    physical interaction engine after semantic proof; Playwright MCP is deterministic
    fallback/verification. Optional WebUI-style conveniences (own-browser CDP
    attachment, recordings, traces, downloads and session history) are evidence/session
    features, not mutation bypasses.
    """
    enabled: bool = True
    attach_same_browser: bool = True
    # When true with cdp_url, the deterministic HIP executor also attaches to that
    # already-running Microsoft Edge/Chromium instance instead of launching a second browser. This is the HIP
    # equivalent of browser-use/web-ui's "Use Own Browser" mode.
    use_own_browser: bool = False
    cdp_url: str = ""  # empty = derive from mcp.playwright_mcp_remote_debugging_port
    keep_alive: bool = True
    keep_browser_open: bool = True
    # Important for corporate Edge/SSO: Browser-Use uses its own CDP websocket
    # client. Do not attach it while Dell SSO is in progress.  Normal filling is
    # owned by the deterministic Playwright context; Browser-Use attaches only when
    # recovery perception is requested.
    startup_attach: bool = False
    recovery_only_attach: bool = True
    # In a HIP-managed browser, browser-use is never allowed to own/reset the
    # lifecycle.  Browser-Use-style perception is produced from the existing
    # Playwright semantic state instead. Set false only for an explicitly external
    # own-browser session where independent Browser-Use CDP attachment is desired.
    safe_managed_browser_mode: bool = True
    non_invasive_detach: bool = True
    attach_timeout_seconds: float = 8.0
    snapshot_timeout_seconds: float = 4.0
    detach_after_recovery: bool = True
    use_state_snapshot_for_recovery: bool = True
    dynamic_repeatable_rows: bool = True
    fail_open_if_unavailable: bool = True
    max_state_chars: int = 60000
    # Optional Browser-Use WebUI-style browser evidence. Recording/trace are off by
    # default because they may be large and can contain portal content.
    record_video: bool = False
    record_video_dir: str = ""
    record_video_width: int = 1440
    record_video_height: int = 950
    capture_playwright_trace: bool = False
    trace_dir: str = ""
    trace_screenshots: bool = True
    trace_snapshots: bool = True
    trace_sources: bool = False
    save_downloads: bool = True
    download_dir: str = ""
    save_session_history: bool = True
    history_dir: str = ""
    capture_state_on_phase_transition: bool = True
    capture_state_after_actions: bool = False
    max_history_entries: int = 400

    # Optional browser-use/web-ui sidecar. The Gradio WebUI is useful for manual
    # observation, persistent own-browser sessions and debugging, but HIP execution
    # does not depend on its UI internals. The agent integrates browser-use core
    # directly through CDP; these fields only launch/describe the optional sidecar.
    webui_enabled: bool = False
    webui_repo_dir: str = "./third_party/browser-use-web-ui"
    webui_host: str = "127.0.0.1"
    webui_port: int = 7788




class MLflowConfig(BaseModel):
    """Asynchronous MLflow tracking for agent/mission observability only."""
    enabled: bool = True
    tracking_uri: str = ""  # empty -> MLFLOW_TRACKING_URI or MLflow default
    experiment_name: str = "HIP Portal Agent"
    run_name_prefix: str = "hip"
    async_logging: bool = True
    fail_open: bool = True
    system_metrics: bool = False
    log_artifacts: bool = False
    log_event_artifact: bool = True
    flush_timeout_seconds: float = 8.0
    max_pending_operations: int = 2048

class UniversalOperatorConfig(BaseModel):
    """General governed portal-learning operator.

    The operator may learn unfamiliar page families and visible capabilities, but
    live page evidence remains authoritative and mutations still require the
    explicit three-part mutation gate. Persisted learning is semantic/value-free.
    """
    enabled: bool = True
    deep_learning_enabled: bool = True
    max_execution_steps: int = 40
    max_form_fill_cycles: int = 8
    max_safe_discovery_actions: int = 12
    learn_unknown_page_families: bool = True
    learn_unknown_actions: bool = True
    require_exact_input_coverage: bool = True
    require_live_reproof_every_action: bool = True
    persist_values: bool = False


class SkillInductionConfig(BaseModel):
    """Validated semantic skill induction for faster future portal execution.

    Skills retain only value-free workflow structure and semantic control identity.
    Current input.json values and current live-page evidence remain authoritative.
    """
    enabled: bool = True
    fast_replay_enabled: bool = True
    memory_subdir: str = "induced_skills"
    min_verified_successes: int = 1
    min_replay_confidence: float = 0.66
    min_match_score: float = 0.45
    confidence_half_life_days: float = 45.0
    stale_after_days: float = 120.0
    demote_after_failures: int = 2
    max_skills: int = 500
    prefer_skill_semantics_for_form_binding: bool = True
    require_exact_verification_for_induction: bool = True
    require_live_reproof_on_replay: bool = True
    store_values: bool = False
    store_selectors: bool = False
    store_coordinates: bool = False


class ReplayPolicyConfig(BaseModel):
    """Replay-based exploration/exploitation policy improvement.

    Historical executions are treated as a value-free replay world. The agent
    scores whether an old run actually achieved the requested goal, filled every
    input-owned value, verified repeatable rows and completed the intended action.
    Offline dreaming selects an improved search policy for later runs, while
    every real browser action still requires current live-page proof.
    """
    enabled: bool = True
    memory_subdir: str = "replay_policy"
    import_old_runs: bool = True
    max_old_runs_to_import: int = 200
    max_replay_episodes: int = 5000
    dreaming_enabled: bool = True
    dream_after_every_run: bool = True
    min_replay_support: int = 2
    exploitation_min_confidence: float = 0.78
    exploitation_min_success_rate: float = 0.80
    hybrid_min_confidence: float = 0.50
    semantic_policy_match_threshold: float = 0.55
    min_action_replay_support: int = 2
    action_exploitation_threshold: float = 0.70
    prefer_fast_replay_when_exploiting: bool = True
    force_exploration_on_drift: bool = True
    live_reproof_required: bool = True
    store_values: bool = False
    store_selectors: bool = False
    store_coordinates: bool = False




class ModelPortfolioConfig(BaseModel):
    """On-Prem Dell AIA champion/challenger model routing."""
    enabled: bool = True
    memory_subdir: str = "model_portfolio"
    on_prem_only: bool = True
    parallel_models: int = 3
    max_parallel_models: int = 6
    # R12: learning and complex-task reasoning deliberately use a portfolio;
    # single-model collapse is reserved for repeatedly proven exploitation.
    learning_parallel_models: int = 4
    complex_task_parallel_models: int = 4
    min_distinct_models_during_learning: int = 2
    force_multi_model_during_learning: bool = True
    force_multi_model_for_complex_tasks: bool = True
    fast_exploitation_single_model: bool = True
    disable_single_model_collapse_during_learning: bool = True
    shadow_reward_weight: float = 0.35
    benchmark_low_confidence_only: bool = False
    availability_probe_enabled: bool = True
    availability_probe_on_task_start: bool = True
    availability_probe_ttl_seconds: int = 1800
    availability_probe_parallelism: int = 4
    record_usage_ledger: bool = True
    min_champion_trials: int = 3
    min_champion_score: float = 0.78
    text_models: List[str] = Field(default_factory=lambda: [
        "gpt-oss-120b", "gpt-oss-20b", "mistral-small-3-1-24b-instruct-2503",
        "llama-3-3-70b-instruct", "gemma-3-27b-it", "llama-3-2-3b-instruct",
    ])
    vision_models: List[str] = Field(default_factory=lambda: [
        "gemma-3-27b-it", "pixtral-12b-2409", "florence-2-large-ft",
    ])
    embedding_models: List[str] = Field(default_factory=lambda: ["nomic-embed-vision-v1-5"])
    persist_customer_values: bool = False
    persist_selectors: bool = False
    persist_coordinates: bool = False


class RecursiveSelfImprovementConfig(BaseModel):
    """Bounded recursive policy/model improvement from verified task rewards."""
    enabled: bool = True
    memory_subdir: str = "recursive_self_improvement"
    max_recursive_cycles: int = 3
    minimum_improvement: float = 0.005
    update_model_portfolio: bool = True
    update_replay_policy: bool = True
    update_skill_confidence: bool = True
    allow_source_code_self_modification: bool = False


class TraceSelfRepairConfig(BaseModel):
    """LLM-assisted bounded recovery from masked runtime traces."""
    enabled: bool = True
    memory_subdir: str = "trace_self_repair"
    max_trace_chars: int = 42000
    auto_repair_enabled: bool = True
    auto_repair_min_confidence: float = 0.72
    max_auto_repair_attempts: int = 2
    update_policy_from_verified_outcome: bool = True
    allow_source_code_self_modification: bool = False


class DeterministicRecipeConfig(BaseModel):
    """Promote repeatedly verified successful trajectories into script-like semantic recipes."""
    enabled: bool = True
    memory_subdir: str = "deterministic_recipes"
    min_verified_successes: int = 2
    min_average_reward: float = 0.90
    min_match_score: float = 0.72
    demote_after_failures: int = 2
    prefer_recipe_before_skill_replay: bool = True
    live_reproof_required: bool = True
    store_values: bool = False
    store_selectors: bool = False
    store_coordinates: bool = False


class ContinuousLearningConfig(BaseModel):
    """Learn the portal continuously from real fill/click/navigation experience."""
    enabled: bool = True
    memory_subdir: str = "continuous_learning"
    learn_from_fill: bool = True
    learn_from_click: bool = True
    learn_from_navigation: bool = True
    learn_from_search: bool = True
    learn_failed_actions_as_negative_evidence: bool = True
    promote_only_after_exact_judge_human_pass: bool = True
    max_actions_per_phase: int = 1000
    persist_customer_values: bool = False
    persist_selectors: bool = False
    persist_coordinates: bool = False


class HumanInTheLoopConfig(BaseModel):
    """Supervised field binding and one-time learning-phase verdict review."""
    enabled: bool = True
    memory_subdir: str = "human_teaching"
    min_task_similarity: float = 0.35
    create_assistance_request_on_unresolved: bool = True
    require_human_for_ambiguous_mapping: bool = True
    allow_click_to_teach: bool = True
    # V243R11: interactive demonstration mode. The operator can navigate/click
    # through the real HIP page while the browser captures semantic actions.
    allow_interactive_demonstration: bool = True
    interactive_demo_requires_final_exact_reproof: bool = True
    interactive_demo_promote_after_human_pass: bool = True
    verify_taught_binding_with_exact_readback: bool = True
    interactive_wait_seconds: int = 0
    # V243R6: newly learned HIP phases pause once after automated judging so a
    # human can confirm either a PASS or a BLOCKED result. The resolved verdict is
    # persisted as teaching evidence and is not requested again for that run+phase.
    review_newly_learned_phase_once: bool = True
    review_on_judge_pass: bool = True
    review_on_judge_block: bool = True
    phase_review_wait_seconds: int = 600
    phase_review_poll_seconds: float = 2.0
    # V243R7 completion-first hold policy. A blocked/incomplete phase keeps the
    # same browser/session alive and waits for supervised recovery instead of
    # closing Chrome and emitting a half-complete final report. A zero timeout
    # means wait indefinitely until the operator resolves the recovery request
    # or explicitly stops the controller process.
    hold_browser_on_incomplete_phase: bool = True
    incomplete_phase_wait_seconds: int = 0
    incomplete_phase_poll_seconds: float = 2.0
    keepalive_seconds: float = 20.0
    never_finalize_incomplete_run: bool = True
    multi_model_judge_on_disagreement: bool = True
    multi_model_judge_force_during_learning: bool = True
    human_pass_requires_exact_evidence: bool = True
    store_values: bool = False
    store_selectors: bool = False
    store_coordinates: bool = False




class PersistentOperatorConfig(BaseModel):
    """Open-ended goal convergence for arbitrary HIP search/fill/edit tasks.

    ``max_goal_cycles=0`` means there is no attempt-count termination. Stagnation
    escalates to model/human assistance while the authenticated browser remains
    alive; it does not declare the user goal complete.
    """
    enabled: bool = True
    max_goal_cycles: int = 0
    require_final_human_confirmation: bool = True
    human_after_failed_cycles: int = 3
    human_wait_seconds: int = 0
    poll_seconds: float = 2.0
    keep_browser_open: bool = True
    deep_learn_unknown_family: bool = True
    update_rsi_after_every_cycle: bool = True
    use_multi_model_after_failure: bool = True
    synthesize_inline_patch_input: bool = True
    stop_file_name: str = "STOP_HIP_OPERATOR"
    preserve_customer_values_in_memory: bool = False

class ProductionE2EConfig(BaseModel):
    """Production lifecycle controls for the one-command end-to-end operator.

    This layer does not weaken browser/action safety.  It adds static readiness,
    single-session locking, mutation governance, immutable request fingerprints,
    hash-chained execution journaling, and safe review bundles around the existing
    Universal Portal Operator.
    """
    enabled: bool = True
    single_active_browser_session: bool = True
    lock_filename: str = ".hip_runtime/production_execution.lock"
    lock_stale_seconds: int = 14400
    lock_heartbeat_seconds: int = 30
    min_free_disk_mb: int = 512
    require_autogen_075: bool = True
    require_input_json_when_fill_requested: bool = True
    require_live_runtime_certificate_for_mutation: bool = True
    require_golden_reference_for_known_phase_tasks: bool = False
    require_operator_role_for_mutation: bool = True
    require_approval_id_for_mutation: bool = False
    require_execution_input_immutability: bool = True
    require_governance_ledger_integrity_for_mutation: bool = True
    capture_mutation_before_after_evidence: bool = True
    create_safe_review_bundle: bool = True
    safe_review_bundle_name: str = "SAFE_REVIEW_BUNDLE.zip"
    keep_sensitive_screenshots_out_of_safe_bundle: bool = True
    safe_review_strict_allowlist: bool = True
    safe_review_manifest_filename: str = "production_safe_review_manifest.json"
    fail_on_stale_execution_lock: bool = True
    write_hash_chained_journal: bool = True
    journal_filename: str = "production_execution_journal.jsonl"
    write_request_manifest: bool = True
    request_manifest_filename: str = "production_request_manifest.json"
    write_execution_integrity_receipt: bool = True
    execution_integrity_filename: str = "production_execution_integrity.json"
    write_failure_diagnostics: bool = True
    failure_diagnostics_filename: str = "production_failure_diagnostics.json"
    write_final_summary: bool = True
    final_summary_filename: str = "production_final_summary.json"


class LiveRuntimeCertificationConfig(BaseModel):
    """Strict live-environment certificate consumed by the Live GO/NO-GO gate.

    v2.2.6 can renew a missing/expired/mismatched certificate automatically when
    Live GO/NO-GO is requested. The renewal is still the same non-mutating real
    Windows certification; it simply removes the manual stale-certificate step.
    """
    enabled: bool = True
    require_for_live_go_no_go: bool = False
    ttl_seconds: int = 3600
    # Hybrid executor policy: Playwright MCP is sufficient for a live certificate;
    # PyAutoGUI MCP upgrades the run to physical desktop-primary execution when present.
    require_pyautogui_mcp: bool = False
    latest_certificate_relative_path: str = ".hip_runtime/live_runtime_certificate.json"
    auto_refresh_on_live_readiness: bool = True
    auto_refresh_only_when_mission_idle: bool = True



class AppConfig(BaseModel):
    portal: PortalConfig = Field(default_factory=PortalConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    navigation: NavigationConfig = Field(default_factory=NavigationConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)
    brain: PortalBrainConfig = Field(default_factory=PortalBrainConfig)
    portal_learning: PortalLearningConfig = Field(default_factory=PortalLearningConfig)
    autonomous_form: AutonomousFormConfig = Field(default_factory=AutonomousFormConfig)
    runtime_self_heal: RuntimeSelfHealConfig = Field(default_factory=RuntimeSelfHealConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    aia: AIAConfig = Field(default_factory=AIAConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    browser_use: BrowserUseConfig = Field(default_factory=BrowserUseConfig)
    autowebglm: AutoWebGLMConfig = Field(default_factory=AutoWebGLMConfig)
    semantic_understanding: SemanticUnderstandingConfig = Field(default_factory=SemanticUnderstandingConfig)
    langchain_browser_toolkit: LangChainBrowserToolkitConfig = Field(default_factory=LangChainBrowserToolkitConfig)
    expert_skills: ExpertSkillConfig = Field(default_factory=ExpertSkillConfig)
    pyautogui: PyAutoGUIConfig = Field(default_factory=PyAutoGUIConfig)
    vision_runtime: VisionRuntimeConfig = Field(default_factory=VisionRuntimeConfig)
    live_runtime_certification: LiveRuntimeCertificationConfig = Field(default_factory=LiveRuntimeCertificationConfig)
    mlflow: MLflowConfig = Field(default_factory=MLflowConfig)
    universal_operator: UniversalOperatorConfig = Field(default_factory=UniversalOperatorConfig)
    skill_induction: SkillInductionConfig = Field(default_factory=SkillInductionConfig)
    replay_policy: ReplayPolicyConfig = Field(default_factory=ReplayPolicyConfig)
    model_portfolio: ModelPortfolioConfig = Field(default_factory=ModelPortfolioConfig)
    recursive_self_improvement: RecursiveSelfImprovementConfig = Field(default_factory=RecursiveSelfImprovementConfig)
    trace_self_repair: TraceSelfRepairConfig = Field(default_factory=TraceSelfRepairConfig)
    deterministic_recipe: DeterministicRecipeConfig = Field(default_factory=DeterministicRecipeConfig)
    continuous_learning: ContinuousLearningConfig = Field(default_factory=ContinuousLearningConfig)
    human_in_the_loop: HumanInTheLoopConfig = Field(default_factory=HumanInTheLoopConfig)
    persistent_operator: PersistentOperatorConfig = Field(default_factory=PersistentOperatorConfig)
    production_e2e: ProductionE2EConfig = Field(default_factory=ProductionE2EConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)


def load_config(path: str | Path) -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = _expand(raw)
    return AppConfig.model_validate(raw)
