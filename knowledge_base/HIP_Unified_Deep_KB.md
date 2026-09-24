# HIP Unified Deep Knowledge Base with Knowledge Graph
**Version:** v80.019-unified-runs  
**Sources inventoried:** 552  
**Reports catalogued:** 268  
**Knowledge Graph:** 387 nodes / 468 edges
> Credentials, raw tokens, direct personal emails, private account identifiers and private IP addresses are redacted. The corrected build is not yet verified by a new live Dell DEV rerun.
## Executive Summary
This consolidated KB merges the canonical HIP knowledge, form-fill skill, portal learning, runtime reports, U-HAUL golden evidence, interface templates, API knowledge and the v80.018 captured-live corrections. The execution contract is: canonical input determines exact values; an interface/topology-aware planner builds the dependency DAG; Playwright MCP performs real actions; a section/stage judge verifies committed values; evidence and the Knowledge Graph explain the run; only verified behavior is learned.
## Authority Order
1. **Captured live action/runner evidence** — Highest for what actually happened in a run
2. **v80.018 hard gates and live-correction report** — Highest current corrective behavior
3. **hip_form_fill_skill.json** — Current page fields, aliases, order, conditions and known bugs
4. **HIP_Canonical_KB_MASTER.json** — Domain, workflows, UI behavior, API and historical examples
5. **Golden U-HAUL reference evidence** — Proven reusable SFTP-HAFT pattern, not universal across interfaces
6. **Older KB/transcripts/manuals** — Context; superseded when contradicted by higher-priority evidence

## Canonical Corrections
| Topic | Legacy/conflict | Canonical | Why |
|---|---|---|---|
| Deployment groups | dce-default-sender / dce-default-receiver in older U-HAUL input | dce-shared-sender / dce-shared-receiver | Direction/deployment rule introduced in v79.81 and retained by later canonical config. |
| Transaction Type attribute derived_from | ELEMENT_IN_PAYLOAD in older source document-type input | TRANSACTION_ROOT_ELEMENT; Expression hidden/not filled | Portal screenshot evidence overrides workbook/config-manual value. |
| Create route | Assume /create or /new URL | Open list page, click + Add/Create, remain on same page/drawer/wizard | Confirmed page behavior and route safety rule. |
| Rule condition row handling | Fill row indexes before proving the + action created rows | Click scoped Conditions +, recount visible rows, fill row-scoped cells, verify both rows, then unlock Mapping Identifier | v80.018 hard gate based on captured live evidence. |
| Transport identity | Continue when Partner/System identity is absent or unreadable | System Type → wait dependent identity → fill exact System/Partner → committed-value verification → only then downstream fields | v80.018 dependency gate and fail-closed behavior. |

## HIP Modules
| Module | Purpose | Pages |
|---|---|---|
| BizLink | Business-facing setup and partner/system-related navigation | Partners, Systems, Notifications, Support |
| SecureLink | Core configuration objects used by HIP runtime | Data Maps, Document Types, Rules, Transport Profiles |
| BizExchange | Flow orchestration | Biz Flows, File Exchange, Batch |
| TransTrack | Operational trace utilities | Track and Trace, Firewall Utility |
| BizMon | Monitoring and health visibility | Transaction Manager, Certificate Monitor, Health Monitor, Service Account Monitor |

## Canonical Object Chain
Source Transport Profile → Source Document Type → Rule → Data Map → Target Document Type → Target Transport Profile

## Portal URLs
| Page | URL |
|---|---|
| Document Types | `https://developer.dell.com/hybrid-integrations/securelink/doctypes` |
| Data Maps | `https://developer.dell.com/hybrid-integrations/securelink/datamaps` |
| Rules | `https://developer.dell.com/hybrid-integrations/securelink/rules` |
| Transport Profiles | `https://developer.dell.com/hybrid-integrations/securelink/transportprofiles` |
| Biz Flows | `https://developer.dell.com/hybrid-integrations/bizexchange/bizflows` |
| Partners | `https://developer.dell.com/hybrid-integrations/bizlink/partner` |
| Systems | `https://developer.dell.com/hybrid-integrations/bizlink/system` |

## Detailed Portal Page Models
### Data Map
- Path: SecureLink > Data Maps > + Add
- URL pattern: `/securelink/datamaps`
| Field/section | input.json key | Action | Condition | Notes |
|---|---|---|---|---|
| Map Identifier | `data_map.map_identifier` | fill |  |  |
| Map Identifier Version | `data_map.map_identifier_version` | fill |  |  |
| Map Name | `data_map.map_name` | fill |  |  |
| Map Class | `data_map.map_class` | fill |  |  |
| Contivo Version | `data_map.contivo_version` | fill |  |  |
| Map Data | `data_map.map_data_file` | upload_file |  |  |
| Cross Reference Table details | `data_map.cross_reference_table_details` | toggle |  |  |
### Source Document Type
- Path: SecureLink > Document Types > + Add
- URL pattern: `/securelink/doctypes`
| Field/section | input.json key | Action | Condition | Notes |
|---|---|---|---|---|
| Name | `source_document_type.name` | fill |  |  |
| Transaction Type | `source_document_type.transaction_type` | fill |  |  |
| Version | `source_document_type.version` | fill |  |  |
| Data Format Type | `source_document_type.data_format_type` | select_option |  |  |
| Description | `source_document_type.description` | fill |  |  |
| Operation | `source_document_type.document_identifier.operation` | select_option |  |  |
| Document Identifier rows | `source_document_type.document_identifier.rows` | fill_doc_id_rows |  | Row 0: Derived From + Value. Row 1+: click '+' then find empty select via JS. TRANSACTION_ROOT_ELEMENT rows have no Expression field. |
| Attributes to Configure | `source_document_type.attributes_to_configure` | fill_attributes |  | Transaction Type attribute always uses TRANSACTION_ROOT_ELEMENT — do not fill Expression for it. |
| Validation Type | `source_document_type.validation_type` | select_option |  |  |
### Target Document Type
- Path: SecureLink > Document Types > + Add
- URL pattern: `/securelink/doctypes`
| Field/section | input.json key | Action | Condition | Notes |
|---|---|---|---|---|
| Name | `target_document_type.name` | fill |  |  |
| Transaction Type | `target_document_type.transaction_type` | fill |  |  |
| Version | `target_document_type.version` | fill |  |  |
| Data Format Type | `target_document_type.data_format_type` | select_option |  |  |
| Document Identifier rows | `target_document_type.document_identifier.rows` | fill_doc_id_rows |  | EDIX12: use ELEMENT_IN_PAYLOAD rows with Expression. XML: TRANSACTION_ROOT_ELEMENT (no Expression). |
| Segment Separator | `target_document_type.segment_separator` | fill | data_format_type=EDIX12 |  |
| Data Element Separator | `target_document_type.data_element_separator` | fill | data_format_type=EDIX12 |  |
| Attributes to Configure | `target_document_type.attributes_to_configure` | fill_attributes |  |  |
### Rule
- Path: SecureLink > Rules > + Add
- URL pattern: `/securelink/rules`
| Field/section | input.json key | Action | Condition | Notes |
|---|---|---|---|---|
| Name | `rule.name` | fill |  |  |
| Document Type Name (Version) | `rule.document_type_name_version` | select_option |  |  |
| Rule Type | `rule.rule_type` | select_option |  |  |
| Rule Scope | `rule.rule_scope` | select_option |  |  |
| Description | `rule.description` | fill |  |  |
| Execute Action(s) When | `rule.conditions.execute_actions_when` | select_option |  |  |
| Condition rows | `rule.conditions.rows` | fill_condition_rows |  |  |
| Action Name | `rule.actions.action_name` | fill |  |  |
| Action Type | `rule.actions.action_type` | select_option |  |  |
| Mapping Identifier | `rule.actions.mapping_identifier_name_version` | select_option |  |  |
### Source Transport Profile
- Path: SecureLink > Transport Profiles > + Add
- URL pattern: `/securelink/transportprofiles`
- **Critical:** Use label='Profile Name' ONLY for the profile name field. The generic label='Name' matches the wrong element. Confirmed bug in portal run 2026-04-22.
| Field/section | input.json key | Action | Condition | Notes |
|---|---|---|---|---|
| System Type | `source_transport_profile_dell.system_type` | select_option |  |  |
| System Name | `source_transport_profile_dell.system_name` | select_option |  |  |
| Profile Name | `source_transport_profile_dell.profile_name` | fill |  |  |
| Profile Usage | `source_transport_profile_dell.profile_usage` | select_option |  |  |
| Deployment Group | `source_transport_profile_dell.deployment_group` | select_option |  |  |
| Interface Type | `source_transport_profile_dell.interface_type` | select_option |  |  |
| Interface Environment | `source_transport_profile_dell.interface_environment` | select_option |  |  |
| Existing Account | `source_transport_profile_dell.existing_account` | radio |  |  |
| Existing Account Name | `source_transport_profile_dell.existing_account_name` | fill | existing_account=Yes |  |
| Use Existing Folder | `source_transport_profile_dell.use_existing_folder` | radio |  |  |
| Subscription Folder | `source_transport_profile_dell.subscription_folder` | fill | use_existing_folder=No |  |
| File Filtering Pattern | `source_transport_profile_dell.file_filtering_pattern` | fill |  |  |
| Post Transfer Action | `source_transport_profile_dell.post_transfer_action` | select_option |  |  |
| Document Type | `source_transport_profile_dell.document_type` | select_option |  |  |
### Target Transport Profile
- Path: SecureLink > Transport Profiles > + Add
- URL pattern: `/securelink/transportprofiles`
- **Critical:** Same Profile Name selector issue as source TP — use label='Profile Name' only.
| Field/section | input.json key | Action | Condition | Notes |
|---|---|---|---|---|
| System Type | `target_transport_profile.system_type` | select_option |  |  |
| Partner Name | `target_transport_profile.partner_name` | select_option |  |  |
| Profile Name | `target_transport_profile.profile_name` | fill |  |  |
| Profile Usage | `target_transport_profile.profile_usage` | select_option |  |  |
| Existing Account | `target_transport_profile.existing_account` | radio |  |  |
| Existing Account Name | `target_transport_profile.existing_account_name` | fill | existing_account=Yes |  |
| Use Existing Folder | `target_transport_profile.use_existing_folder` | radio |  |  |
| Existing Folder | `target_transport_profile.existing_folder` | fill | use_existing_folder=Yes |  |
| File Filtering Pattern | `target_transport_profile.file_filtering_pattern` | fill |  |  |
| Post Transfer Action | `target_transport_profile.post_transfer_action` | select_option |  |  |
| Document Type | `target_transport_profile.document_types` | select_option |  |  |
### Biz Flow
- Path: BizExchange > Biz Flows > + Add
- URL pattern: `/bizexchange/bizflows`
| Field/section | input.json key | Action | Condition | Notes |
|---|---|---|---|---|
| Business Flow Name | `biz_flow.flow_details.business_flow_name` | fill |  |  |
| Flow Description | `biz_flow.flow_details.flow_description` | fill |  |  |
| Source Type | `biz_flow.configure_source.source_type` | select_option |  |  |
| Source Application | `biz_flow.configure_source.source_application` | select_option |  |  |
| Source Transport Profile | `biz_flow.configure_source.source_transport_profile` | select_option |  |  |
| Document Type Name (Version) | `biz_flow.configure_source.document_type_name_version` | select_option |  |  |
| Flow Identifier Operator | `biz_flow.configure_source.flow_identifier_operator` | select_option |  |  |
| Flow Identifier Attributes | `biz_flow.configure_source.attributes` | fill_flow_attributes |  |  |
| Target Type | `biz_flow.configure_targets[0].target_type` | select_option |  |  |
| Target Application | `biz_flow.configure_targets[0].target_application` | select_option |  |  |
| Target Transport Profile | `biz_flow.configure_targets[0].target_transport_profile` | select_option |  |  |
| Document Type Name (Version) | `biz_flow.configure_targets[0].document_type_name_version` | select_option |  |  |
| Process Steps | `biz_flow.configure_targets[0].process_steps` | fill_process_steps |  |  |
| Name | `biz_flow.configure_routing.rule_name` | fill |  | This creates a new routing rule inline. The saved value populates configure_routing.rules in the comparator. |
| Rule Type | `biz_flow.configure_routing.rule_type` | select_option |  |  |
| Rule Scope | `biz_flow.configure_routing.rule_scope` | select_option |  |  |
| Execute Action(s) When | `biz_flow.configure_routing.conditions.execute_actions_when` | select_option |  |  |
| Condition rows | `biz_flow.configure_routing.conditions.rows` | fill_condition_rows |  |  |
| Action Name | `biz_flow.configure_routing.actions.action_name` | fill |  |  |
| Action Type | `biz_flow.configure_routing.actions.action_type` | select_option |  |  |
| Target | `biz_flow.configure_routing.actions.target` | select_option |  |  |

## Hard Gates
| Area | Gate | Rule |
|---|---|---|
| Document Type | Derived From | Expression shown only for ELEMENT_IN_PAYLOAD or FILENAME; hidden for TRANSACTION_ROOT_ELEMENT/PAYLOAD_CONTAINS |
| Document Type | Transaction Type attribute | Always TRANSACTION_ROOT_ELEMENT; no Expression fill |
| Data Map | Map Data | JAR path must exist, upload must be proven, then page verification |
| Rule | Condition rows | Scoped Conditions + → visible-row recount → row-scoped fill → exact verification |
| Rule | Mapping Identifier | Hard-blocked until both required conditions verify |
| Transport | Identity | System Type → dependent System/Partner field appears → exact identity commits |
| Transport | Downstream fields | Profile Name, account/folder and Document Type are not trusted before identity gate |
| BizFlow | Wizard Next | Never advance until current stage validation and committed values verify |
| Full plan | Required failure | Stop immediately for failed/needs_fix/needs_review_identity/missing_required_field/verification_failed unless explicit continue-anyway |
| Learning | Promotion | Only verified committed actions; failed, ambiguous or selector-reuse paths are quarantined |

## Interface Matrix
| Interface | Aliases | Field family | Support | Verification |
|---|---|---|---|---|
| SFTP-HAFT | SFTP HAFT, SFTP_HAFT, SFTP | System Type → identity → Profile Name → Usage → Deployment Group → Interface Type/Environment → account/folder/filter/action → Document Type | Translation and passthrough; single or multi-branch | Golden U-HAUL reference exists; current v80.018 re-run on live Dell DEV not verified |
| FTP/FTE | FTP, FTE | Same account/folder family as SFTP; normalized interface-aware plan | Translation and passthrough | Offline/fake MCP and templates; live Dell DEV not verified |
| HTTPS-AS2 | AS2, HTTPS AS2 | Partner source with POST, AS2 IDs/certificate fields; Dell Application target with target interface/environment | EDI→XML, XML→EDI, passthrough; Process Steps and Routing required where applicable | Workflow document mapped and offline/fake MCP tested; live Dell DEV not verified |

## Agent Architecture
- **Streamlit UI:** Entry surface for Input Builder, Chat, single-task and full-plan runs
- **Canonical Config:** Single source for input.json, Excel, provenance, overrides and judge
- **Deterministic Planner:** Builds interface/flow/topology-aware task DAG
- **MCPProfileRunnerRouter:** Only live mutating entry point
- **MCP Portal Page Learner:** Captures snapshot, screenshot, DOM/event/network evidence before fill
- **PortalFormModel:** Sections, row-scoped fields, buttons, dropdowns, confidence and evidence
- **Typed HIP Action Queue:** Deterministic actions instead of random browser calls
- **Playwright MCP Runtime:** Real refs and schema-shaped browser actions
- **Section/Stage Judge:** Text + visual/evidence verification before progression
- **MCPToolLogger:** Durable steps, screenshots, snapshots, network, summaries and reports
- **Checkpoint/Resume:** Restores last verified state and bounds recovery
- **Verified-only Learning Bank:** Promotes only committed verified strategies
- **Selector Quarantine:** Blocks failed/reused selectors from poisoning replay
- **Knowledge Graph:** Explains plans, dependencies, actual actions, failures and fixes
- **API-first Orchestrator:** Uses supported APIs where available, UI/MCP otherwise

## Knowledge Graph
- Nodes: 387
- Edges: 468
- Node types: APIEndpoint (15), AgentComponent (15), CanonicalPortalPage (7), ConfigurationObject (7), DependencyRule (6), EvidenceSource (4), FailureMode (4), FlowType (5), Gate (8), ImplementationReport (69), InputField (80), InterfaceType (3), KnowledgeSource (2), LifecycleStep (9), OrchestrationAPI (3), Platform (1), PortalField (80), PortalModule (5), PortalPage (17), Recovery (4), ReferenceExample (1), ReferenceObject (7)
- Relation types: AUTOMATED_BY_COMPONENT (15), BLOCKS_UNTIL (7), CANONICAL_CHAIN_NEXT (5), CAN_INVOKE (15), COMPLEMENTS (1), CONTAINS_MODULE (5), CONTAINS_PAGE (17), CREATES_OBJECT (7), DOCUMENTS (69), ENFORCES (1), EXECUTION_FLOW_NEXT (12), GROUNDS (6), HAS_CANONICAL_PAGE (7), HAS_FIELD (80), HAS_LIFECYCLE_STEP (9), HAS_REFERENCE_OBJECT (7), INSTANCE_OF (7), MAPS_TO_INPUT_FIELD (80), NEXT_STEP (8), PRECEDES (2), PREFERS (3), PROTECTS (1), PROVES (4), RECOGNIZED_BY (1), RECOVERED_BY (4), REFERENCED_BY (1), REVEALS_OR_ENABLES (6), SOURCE_OF (1), SUPPORTS_FLOW_TYPE (5), SUPPORTS_INTERFACE (3), TARGET_OF (1), USED_BY (1), USES_INTERFACE (1)

### Supported Queries
- What object or stage depends on this field?
- Why did a stage fail and what recovery is recommended?
- Which portal page and action creates an object?
- Which input.json key maps to a portal field?
- Which interface/flow type supports a plan?
- Which evidence source justifies a rule?
- What was the last verified stage and what is blocked next?

## Two-Run Evidence Review

Both added U-HAUL full-dummy runs failed end-to-end. They were reviewed against screenshots, DOM text, fill booleans, validation messages and run manifests. Correct information was added only as guarded knowledge.

- Both runs used **Chrome DevTools MCP**, not Playwright MCP.
- Vision verification was **not configured** in either run.
- Document Type `pass` verdicts were false positives: all 23 attempts were false and the evidence was the listing page.
- Data Map selection was not accepted success: the map already existed and both XML validation uploads were rejected.
- Rule was incomplete: second condition row and Mapping Identifier were missing.
- Transport Profiles were partial and blocked by duplicate names, legacy deployment groups and missing/unverified controls.
- BizFlow evidence was on the **Create Rule** surface, proving wrong-page/overlay focus.
- Runtime DDS IDs drifted across nearly every matched selector, so semantic re-resolution is mandatory.

See `TWO_RUN_EVIDENCE_REVIEW.md` and `TWO_RUN_EVIDENCE_REVIEW.json` for the full verdict.

## U-HAUL POASN Reference
Golden historical pattern: SFTP-HAFT, one-to-one, outbound 856. Canonical planning applies the deployment-group and Transaction Type overrides listed above. Latest captured live evidence showed the Rule + action absent, transport identities absent and BizFlow falsely completed; v80.018 adds hard stop, grid driver, identity gates, exact committed-value reads and a live-authoritative post-mortem graph.

## API Payload Rules
- Document Type: camelCase, createdBy, documentIdentifier, attributes usage list.
- Map: requestedBy, Base64 mapData, non-null crossReferenceMapList.
- Rule: numeric documentTypeId, ruleConditions/actions, createdBy.
- Transport Profile: transportProfileDetails wrapper, operation=create, interfaceDetails.parameters, documentTypeDetails, requestedBy/workOrderId.
- Flow create: flowDefinition is a JSON string; validate name and use work-order/task lifecycle.
- Flow deploy: workOrderId/taskId/deployedBy or orchestration wrapper; poll history to ACTIVE/FAILED.
- Partner/Account: required requester/account headers; resolve IDs rather than guessing.

## Verification
| Layer | Status |
|---|---|
| Code implementation | YES |
| Offline acceptance | YES — 2119 passed / 4 environment-dependent failures |
| Captured live evidence replay | YES |
| Real-snapshot fixtures | YES |
| Fake MCP full flow | YES |
| Corrected live Dell DEV rerun | NO |
| Genuine Docker/Podman build+health proof | NO in archive |

## Known Bugs
### BUG-001 — Transport Profile (source + target)
- Symptom: Profile Name field filled with wrong value (e.g. doc type name)
- Root cause: Generic label='Name' selector matches first input on the form instead of the Profile Name field
- Fix: Always use label='Profile Name' or label='Profile Name *' — never the generic label='Name'
### BUG-002 — Document Types — Attributes to Configure
- Symptom: Transaction Type attribute filled as ELEMENT_IN_PAYLOAD
- Root cause: Config Manual XLSX col E shows ELEMENT_IN_PAYLOAD for Transaction Type. Portal requires TRANSACTION_ROOT_ELEMENT.
- Fix: Hardcode Transaction Type attribute derived_from = TRANSACTION_ROOT_ELEMENT in skill. Never fill Expression for it.
### BUG-003 — Document Types — Document Identifier row 2+
- Symptom: Row 2 Derived From overwrites row 1 instead of filling the new empty row
- Root cause: Index-based nth-element approach picks wrong element. Elements appear multiple times in list_interactives.
- Fix: Use JS to find the empty select element (isEmpty = !sel.value || sel.selectedIndex <= 0) after clicking '+'
### cfgmanual_block_two_col_marker — Configuration Manual parser (_block)
- Symptom: Configure Targets / Flow Identifiers / Configure Routing came back EMPTY from the Excel.
- Root cause: _block matched the marker only against the FIRST cell, but the manual uses two-column markers like ['TAB','Configure Target(s)'] and ['Section','Flow Identifiers'] — keyword is in the 2nd cell.
- Fix: v79.76: _block now matches the whole joined row; added _section_block + _marker_row for precise Section/TAB boundary matching.
### cfgmanual_val_exact_word — Configuration Manual parser (_val)
- Symptom: Routing rule Name / Version / action Type / Target were empty.
- Root cause: _val('Name\t') did a substring check for 'name\t' in the first cell, but cells contain no tabs, so it never matched the 'Name' row.
- Fix: v79.76: a trailing tab in a keyword now means EXACT first-cell match.
### cfgmanual_passthrough_requires_map — Configuration Manual parser (requires_data_map)
- Symptom: Passthrough flow was flagged requires_data_map=True even though the Data Map section was all 'NA'.
- Root cause: Check was bool(map_identifier); the string 'NA' is truthy.
- Fix: v79.76: treat NA/empty as no-map; detect PASS/PASSTHROUGH in flow name; emit flow_type passthrough|translation.
### cfgmanual_tp_folder_always — Configuration Manual parser (_parse_tp)
- Symptom: subscription_folder + file_filtering_platform were MISSING from both transport profiles.
- Root cause: They were only captured when use_existing_folder=='no'. But the manual lists them under 'Existing Account - Yes' even when use_existing_folder=='yes' (e.g. /tovan/XML/, /Test_ATEA, .*\\*.).
- Fix: v79.77: always capture both; _val returns the first (populated) row under Existing Account - Yes.
### cfgmanual_tp_target_doctype_boundary — Configuration Manual parser (_parse_tp target)
- Symptom: target_transport_profile.document_type came back blank.
- Root cause: Target block boundary stopped at 'Section\tInterface Details - SFTP Server', but the Document(s) Supported → Document Type row sits AFTER that section.
- Fix: v79.77: target TP block now runs to end of sheet.
### cfgmanual_doc_identifier_value_row — Configuration Manual parser (document_identifier_rows)
- Symptom: source_document_type.document_identifier_rows[0].value was the template instruction text instead of 'cXML'.
- Root cause: Took the TRANSACTION_ROOT_ELEMENT marker row's last cell (the '(Mention NA...)' instruction). The real value is on the following 'Value (Mandatory Field)' row.
- Fix: v79.77: after the TRE marker, scan forward to the 'Value' row; reject instruction text; skip NA.
### cfgmanual_label_noise_value — Configuration Manual parser (_val_clean)
- Symptom: target_document_type.validation_type wrongly = 'Selection'.
- Root cause: When the Value column is empty, the last non-empty cell is the Data-Type label ('Selection','Alpha-Numeric',...). _val returned the label.
- Fix: v79.77: _val_clean rejects data-type/label/instruction noise → blank.
### cfgmanual_tx_standard_format — Configuration Manual parser (tx_standard)
- Symptom: tx_standard hardcoded 'X12' even when source is XML/cXML.
- Root cause: Hardcoded literal.
- Fix: v79.77: derive tx_standard from source_document_type.data_format_type (XML→XML, cXML→cXML, EDI→X12, ...); add source_format field. tx_code stays the business code (850).
### cfgmanual_attr_name_misspelling — Configuration Manual parser (_canon_attr_name)
- Symptom: Extracted attribute_name kept the Excel's misspelling 'Reciever'.
- Root cause: The manual misspells 'Receiver' as 'Reciever'; parser copied it verbatim.
- Fix: v79.78: _canon_attr_name maps known HIP attribute-name misspellings (Reciever/Reciver→Receiver, Sendor→Sender, etc.) to the canonical spelling at all 3 extraction sites (doc-type attrs, flow identifiers, routing conditions). Unknown names pass through unchanged.
### output_compare_false_pass_keyname — Output Comparison verdict (render_output_compare + AI prompt + HTML report)
- Symptom: Two completely DIFFERENT files reported PASS ('Perfect match', 0% score, 29 total / 0 matched / 0 in every bucket).
- Root cause: The verdict + display read report keys 'mismatches'/'missing_in_2'/'missing_in_1', but _oc_compare returns 'hard_diffs'/'only_in_1'/'only_in_2'. The wrong keys always came back empty → verdict ALWAYS PASS. The AI prompt and HTML 'Perfect match' banner also branched only on hard_diffs, ignoring disjoint/missing keys and the nothing-matched case.
- Fix: v79.79: verdict now reads the real keys and FAILs on any hard_diffs OR only_in_1/only_in_2 OR (total>0 and matched==0) OR a parse error. AI confirmation prompt only fires on a genuine clean match. HTML banner + badge driven by the verdict. XML parse-error key is now content-hashed so two unparseable files can't falsely match.
### template_compare_html_code_block — Template Compare / Generated input.json preview (Streamlit render)
- Symptom: The completeness summary card showed RAW HTML as text — literal <div>/<span> tags and '✗ Data Map ID' pills — instead of rendering.
- Root cause: The HTML f-string passed to st.markdown(..., unsafe_allow_html=True) was indented to match Python code. Streamlit's CommonMark parser treats any line indented >=4 spaces as a code block, so the HTML rendered as literal text.
- Fix: v79.80: build the card HTML as a single flat (un-indented) string; added _st_html() helper that dedents + strips per-line leading whitespace before st.markdown, and routed all 18 other indented-HTML blocks in the template-compare report through it.
### direction_and_shared_deployment_groups — Input Builder — Config Manual → input.json + Excel generation
- Symptom: Direction was guessed only from a '_OB' token; deployment groups were the old dce-default-*; no way to flip Inbound/Outbound.
- Root cause: No sender/receiver-based direction detection; hardcoded dce-default-sender/receiver.
- Fix: v79.81: added _detect_direction (Partner→Dell=Inbound, Dell→Partner=Outbound + evidence/token fallbacks) and _apply_direction_to_objects which sets source_type/target_type and forces dce-shared-sender / dce-shared-receiver. Added an Inbound/Outbound toggle in the Input Builder that re-derives everything on override. Excel writer accepts both rule/data_map schema shapes so the Rules section is always written.
### master_prompt_judge_canonical_implemented — Input Builder — master agent build spec (LLM Judge + canonical config)
- Symptom: Master spec required LLM-as-Judge scoring, canonical-config-first generation, human_review_queue, full editable field model, and 5 extra Excel sheets — none implemented.
- Root cause: Earlier builds only had direction + deployment groups.
- Fix: v79.82: added _llm_judge_evaluate (0-5 field / 0-100 overall / 4 gates), _build_canonical_config + sender_role/receiver_role, _editable_field_model + _resolve_final_value, judge scorecard + human_review_queue UI in Template Compare, canonical_config/judge/queue downloads, and README/Direction Rules/Editable Overrides/LLM Judge Scorecard/Acceptance Criteria Excel sheets.
### master_prompt_full_implementation — Input Builder — complete master agent build spec (§17/§18/§20/§21/§23)
- Symptom: Modular 14-agent pipeline, 6-stage judge, full §17 HTML report, and extraction_audit outputs were not implemented.
- Root cause: v79.82 had the judge + canonical config but not the staged judge, agent provenance, or full report/audit artifacts.
- Fix: v79.83: added _AuditLogger (per-field evidence+confidence), _run_agent_pipeline (14 agents), _run_staged_judge (6 checkpoints), _build_master_html_report (all 14 §17 sections), _build_extraction_audit_json, and UI to run the pipeline + download extraction_audit_report.html/.json. Full spec coverage 40/40.
### single_tx_upload_full_pipeline — Input Builder — Single Transaction (file upload) mode
- Symptom: Uploading customer files built a read-only skeleton; no Inbound/Outbound, no editable sections, no judge/report — the new features were only in Template Compare.
- Root cause: v79.82/83 features were wired into render_template_compare_tab, not the file-upload mode users actually use.
- Fix: v79.84: after build, the Single Transaction mode now runs _detect_direction + _apply_direction_to_objects (shared deployment groups), shows an Inbound/Outbound toggle, an EDITABLE per-section editor (Data Map / Source DT / Target DT / Rule / Source+Target TP / Biz Flow), the LLM-Judge gate, and downloads for canonical_config / judge scorecard / human_review_queue / extraction_audit_report.html+json — all from the same edited config.
### profile_runner_direction_display — Profile Runner — Pre-fill Review + Task Results
- Symptom: The Profile Runner did not show whether a flow was Inbound or Outbound.
- Root cause: No direction surfaced in the plan/runner UI.
- Fix: v79.85: added _resolve_plan_direction (explicit direction → system_type pair → name token) + _direction_badge_html; the plan is stamped with _direction/_flow_type on build (and backfilled when loaded), and an Inbound/Outbound pill now shows in the Pre-fill Review header and the Task Results header.
### html_alias_unbound_namerror — Input Builder (single-tx) + Template Compare — runtime
- Symptom: NameError: name '_h' is not defined when rendering the single-tx editor / template-compare judge sections.
- Root cause: v79.82/84 used _h.escape() (html alias) inside render_agentic_run_tab and render_template_compare_tab, but `import html as _h` was only present in OTHER functions — these are function-local imports, not module-level.
- Fix: v79.86: added `import html as _h` at the top of both functions. Added a scan to confirm zero remaining unbound _h uses (and checked _jt/_components/_json_sx too).
### input_builder_editable_all_modes — Input Builder — Configuration Manual / Existing Template / file-upload modes
- Symptom: Only the Single Transaction mode could edit each section + generate Excel/input.json. Config Manual mode was read-only (st.json) with no Excel; Template mode only offered a JSON download.
- Root cause: The editable editor + judge + artifact generation were inlined into one mode only.
- Fix: v79.87: extracted _ib_render_editable_config (shared) — direction toggle, EDITABLE per-section editor (Data Map/Source DT/Target DT/Rule/Source+Target TP/Biz Flow), LLM-Judge gate, and downloads for input.json + Configuration Manual Excel + canonical_config + judge scorecard + review queue + HTML audit report. Config Manual mode and Existing Template mode now route through it, so every input source fills correctly, is fully editable, and produces both the Excel and input.json from the edits.
### manual_client_credentials_fallback — API credentials — CLIENT_ID / CLIENT_SECRET
- Symptom: If the .env file couldn't be read, there was no way to supply CLIENT_ID/CLIENT_SECRET; API calls fell back to built-in defaults only.
- Root cause: Credential accessors only checked env vars then hardcoded values.
- Fix: v79.88: added a sidebar 'API Credentials' form (CLIENT_ID, CLIENT_SECRET, optional Token URL, HIP/Workflow service) + a 'Test connection' button. Accessors now resolve UI-entered (session) → .env → built-in, in that order, and the override is overlaid onto the HipApiClient settings so token fetch + deploy use it. Shows the active credential source and warns when .env isn't readable.
### parallel_batch_and_docker_env_and_fast_mode — Performance + throughput + Docker
- Symptom: A single run took ~58 min (Biz Flow step alone ~32 min); no way to run multiple transactions at once; .env had to be mounted.
- Root cause: All flows ran sequentially on one browser; Dockerfile didn't bake credentials.
- Fix: v79.89: (1) Dockerfile now bakes CLIENT_ID/CLIENT_SECRET/token URLs as build ARGs→ENV + writes a baked /app/.env. (2) run_profile_runner_batch_parallel runs N transactions concurrently, each in its own headless BrowserManager with an isolated profile dir (data/browser/parallel/worker-N), via a thread-local manager registry (_BROWSER_TLS) so the shared fill code targets the right browser. UI in the Profile Runner: 'Run multiple transactions in parallel' (1-3 workers). (3) Fast fill mode (sidebar toggle / FAST_FILL_MODE env) trims cosmetic settle waits via _fast_settle.
### list_section_attributeerror — Template Compare + judge + canonical + Excel
- Symptom: AttributeError: 'list' object has no attribute 'get' when a flow has multiple transport profiles (configure_targets / target_transport_profile is a list).
- Root cause: Section accessors used `... or {}` which doesn't catch a list value.
- Fix: v79.90: added _coerce_section_dict (returns first dict of a list, else {}) and applied it to all section accessors in Template Compare, the judge, canonical builder, Excel writer, and the config-manual flow_name/customer derivation.
### sidebar_not_rendered — App shell
- Symptom: Sidebar (App Controls, API Credentials, Fast mode) was not visible.
- Root cause: sidebar_controls() was defined but never called from main().
- Fix: v79.90: main() now calls sidebar_controls() right after init_session(), wrapped in try/except so a sidebar error can't blank the page.
### deploy_edit_already_deployed — Profile Runner — biz_flow_deploy
- Symptom: Deploy reported failure when the flow was actually already deployed (portal shows Edit/Undeploy instead of Deploy).
- Root cause: The deploy step only looked for a Deploy button.
- Fix: v79.90: if Deploy isn't reachable but an Edit/Undeploy/Deployed marker is present on the row, treat as success.
### interface_type_values — Input Builder — Transport Profile editor
- Symptom: interface_type was free text; users didn't know the valid portal values.
- Root cause: No enumeration of the portal's Interface Type options.
- Fix: v79.90: HIP_INTERFACE_TYPES constant + the editor renders interface_type as a dropdown (SFTP HAFT, SFTP Server, HTTPS, HTTPS-AS2, AS2, IBM MQ, Rabbit MQ, Kafka, NAS, S3), keeping any unknown extracted value selectable.
### sidebar_dark_theme_broken — App shell — sidebar styling
- Symptom: Sidebar background was dark blue while Streamlit's buttons/metrics rendered as white cards, so it looked broken (white-on-white text, floating white boxes).
- Root cause: hip_premium_ui_config CSS set the sidebar background to #152b45 (dark) with white text, but didn't restyle the default white button/metric surfaces.
- Fix: v79.91: switched the sidebar to a light theme (#f7f9fc surface, dark #102033 text) matching the rest of the app, and explicitly styled sidebar buttons (white card, blue text), metrics, inputs, and expander headers for readability.
### post_fill_screenshot_capture — Profile Runner — verification
- Symptom: No visual record of what the agent filled on each page.
- Root cause: Feature not present.
- Fix: v79.92: capture a full-page screenshot after each task completes (both run paths), attach the path to the run entry, and show it inline in the task Details + a screenshots gallery. Best-effort — a screenshot failure never breaks the run.
### timing_screenshot_learn_portal — Profile Runner timing + screenshots + Learn tab
- Symptom: No phase breakdown for slow steps; screenshots were a separate section; no way to learn from existing portal config.
- Root cause: Features not present.
- Fix: v79.93: timing phase breakdown (portal load / agent think / fill), per-BizFlow-section screenshots shown in the task log under a collapsible (removed the separate gallery), and a Learn-from-portal button that crawls the portal and updates the KB.
### timing_card_html_code_block — Profile Runner — Run Timing card
- Symptom: Run Timing card leaked raw HTML (<div>/<span> shown as text) instead of rendering the per-task bars + phase breakdown.
- Root cause: v79.93 added a multi-line {_breakdown} HTML block inside an INDENTED triple-quoted f-string for bars_html and the card; Streamlit's CommonMark parser treats lines indented >=4 spaces as a code block, so the HTML rendered literally.
- Fix: v79.94: rebuilt bars_html, the main Run Timing card, and the skill-update card as single flat (un-indented) strings. Verified the rendered card is one line with zero >=4-space-indented lines.
### phase_breakdown_other_was_fill — Profile Runner — Run Timing breakdown
- Symptom: Breakdown showed agent think 0.0s, fill 0.0s, and a huge 'other' (e.g. 32m18s on Biz Flows) — uninformative.
- Root cause: v79.93 only wrapped a narrow LLM call + page nav; the actual field-fill work was never timed, so it all fell into 'other'. The agent fills deterministically (no LLM), so agent_think was ~0.
- Fix: v79.95: compute phases as a clean decomposition — page_load + agent_think measured directly, fill = total task wall time − page_load − agent_think. Removed the 'other' line; relabeled to '⌨️ fill / form work'. Now 'fill' is the real number that explains slow steps.
### fast_mode_now_effective — Performance — fill speed
- Symptom: Fast mode barely changed fill time (only 3 cosmetic sleeps were trimmed).
- Root cause: 172 per-field asyncio.sleep calls, only 3 routed through _fast_settle.
- Fix: v79.95: routed 98 mid-range per-field settle sleeps (0.2–0.8s) through _fast_settle. With Fast mode ON these trim 60%, which can cut a ~32min Biz Flow fill toward ~21min. Short polling sleeps and long critical sleeps left untouched for reliability.
### deploy_failed_despite_edit — Profile Runner — biz_flow_deploy
- Symptom: Deploy step showed Failed (red) even though the flow was already deployed and the portal showed Edit/View/Clone/Migrate.
- Root cause: The already-deployed (Edit) detection only ran AFTER two failed deploy-click attempts and searched too narrowly, so it often didn't fire → status 'partial'/failed.
- Fix: v79.95: added an EARLY deployed-state check right after the row expands — if Edit (or View/Clone/Migrate, no Deploy) is visible, return success immediately → task status 'completed'.
### deterministic_replay_learn_once — Performance — fill speed (learn once)
- Symptom: Agent re-learned the same fields every run (re-ran the full id→label→placeholder cascade), so repeat runs were just as slow.
- Root cause: Session-stable cascade wins (label/placeholder/alias) set _selector_memory but were never persisted for cross-session replay; only replay-path wins were recorded.
- Fix: v79.96: cascade wins on stable strategies now call _record_strategy_replay() → persisted to field_strategy_replay.json. _try_strategy_replay() runs first on every fill and replays the known-good strategy, skipping the cascade; falls back to learning only if the element isn't found. Repeat runs get progressively faster.
### ftp_config_type — Transport Profile — FTP
- Symptom: No support for FTP transport profiles (only SFTP HAFT etc.).
- Root cause: FTP wasn't in the interface-type list, and a normalizer forced interface_type to SFTP HAFT, overriding any other value.
- Fix: v79.96: added FTP to HIP_INTERFACE_TYPES; the normalizer now preserves valid types (FTP/SFTP Server/AS2/...) and only forces SFTP HAFT when blank or a stray AS2 leak. FTP flows through input.json + Excel + Profile Runner like SFTP HAFT.
### interface_type_specific_fields — Transport Profile — all interface types
- Symptom: Agent only knew SFTP-style fields; for HTTPS/HTTPS-AS2/IBM MQ/Kafka it didn't fill the type-specific fields (Protocol, HIP URL, Queue Manager, Kafka Provider, Grant Type, PSE Number, etc.).
- Root cause: No per-interface-type field schema.
- Fix: v79.97: added HIP_INTERFACE_TYPE_FIELDS (full schema from portal screenshots) + _interface_fields_for() resolver (handles conditionals: OAuth2→grant_type, Apikey→pse_number, Kafka Cloudera→bootstrap_servers). The Profile Runner injects the selected type's fields into _stp_field_order so they fill in form order; the Input Builder editor shows only the relevant fields (with dropdowns for known options); FIELD label map updated so the fill engine finds them.
### deep_learn_each_page — Learn tab — live form introspection
- Symptom: Agent's per-interface-type field knowledge depended on hardcoded screenshots; types without a screenshot weren't covered.
- Root cause: Learn-from-portal only scraped list rows + <select>; it didn't open the Create form or cycle interface types.
- Fix: v79.98: added _deep_learn_transport_profile() — opens the Create form, cycles System Type × Interface Type, captures all fields + opens each dropdown for options + records links/notices, saves a live schema, and _interface_fields_for() prefers that learned schema. The agent now learns everything about the Transport Profile page itself.
### deep_learn_wrong_url_and_all_portals — Learn tab — deep-learn
- Symptom: Deep-learn opened the wrong Transport Profile link (/transportprofiles/create) and only covered Transport Profile; Document Types used /documenttypes (wrong).
- Root cause: Hardcoded URLs didn't match the authoritative default_profile_runner_page_urls map; the form is reached via list→Add, not a /create path.
- Fix: v79.99: corrected all URLs (doctypes, datamaps, rules, transportprofiles, bizflows) and reach the form by opening the list page + clicking Add/Create. Added _deep_learn_all_portals() so the button deep-learns EVERY portal form, not just Transport Profile.
### deep_learn_not_clicking_add — Learn tab — deep-learn form opening
- Symptom: Deep-learn navigated to each page and waited but captured 0 fields — it wasn't actually opening the Create form (the '+ Add' button wasn't being clicked).
- Root cause: Deep-learn used a naive page.evaluate regex to find/click an Add button, which didn't match the portal's DDS '+ Add' control.
- Fix: v79.100: added _dl_open_create_form() which reuses the EXACT proven Profile-Runner flow — inspect_page + list_interactives → _v28_agent_find_add_button → _v28_build_selector_for_item → _execute_action_with_target('click') → wait_for_overlay_inputs, with prepare_create_surface + CREATE_ACTION_TARGETS fallbacks. Both _deep_learn_transport_profile and _deep_learn_all_portals now use it, so the form actually opens and fields are captured.
### deep_learn_repeatable_attributes — Learn tab — dynamic form structure
- Symptom: Deep-learn only captured static fields; it didn't learn the inline '+ Add' controls that add/remove repeatable configuration attributes throughout the flow.
- Root cause: Capture only snapshotted the initial form.
- Fix: v79.101: _dl_capture_repeatables() enumerates every inline add/remove control (excluding main Create/Submit/Cancel), clicks each add-control to reveal the repeatable row, and captures the delta fields — so the agent learns what can be added/removed (tags, conditions, actions, multiple TPs, steps) and each row's fields. Wired into the all-portals deep-learn and shown in the Learn tab.
### deep_learn_icon_only_controls — Learn tab — dynamic form structure
- Symptom: Repeatable sections using icon-only controls (no text/aria-label) weren't detected.
- Root cause: Detection relied on text/aria/class matching.
- Fix: v79.102: added unicode-glyph + SVG-href/class icon-hint detection AND a learn-by-effect fallback — click the icon and classify by whether form fields increase (Add) or decrease (Remove). Fully automatic; no need to be told which control.
### deep_learn_advanced — Learn — wizard depth + required flags + validation loop
- Symptom: Deep-learn captured only the first step of multi-step wizards, didn't record required flags, and learning didn't feed back into the builder.
- Root cause: No wizard traversal, no required capture, no validator.
- Fix: v79.103: _dl_walk_wizard walks all wizard steps (Biz Flow); snapshots capture required:true; _validate_against_learned_schema flags configs against the learned portal schema in the Input Builder editor.
### multi_branch_and_as2_fields — Input Builder + Profile Runner — multi-branch flows
- Symptom: Editor only edited the FIRST dict of list sections (multi-branch flows: many target doc types/maps/TPs); HTTPS-AS2 schema lacked the real AS2 fields (AS2 IDs, certificates, MDN).
- Root cause: No list-aware editor; AS2 fields were placeholders pending a real case.
- Fix: v79.104: _render_section_editor handles list sections — one editor per branch + add-branch button (in-place edits, unique widget keys per branch). HTTPS-AS2 schema replaced with the REAL field set from the AMAZON JAPAN case (partner/dell AS2 IDs, certificate + CN/expiry, dell AS2 URL, signing/encryption/MDN). Labels mapped so the Profile Runner fills them.
### stp_field_order_unboundlocal — Profile Runner — transport profile fill
- Symptom: source/target_transport_profile crashed after System Type: stp_section_error UnboundLocalError 'cannot access local variable _stp_field_order' — only 1 field filled.
- Root cause: v79.97 interface-field injection ASSIGNED to _stp_field_order inside the nested async fill function; Python then treats the name as local for the whole function, so the first read (before assignment) raises UnboundLocalError.
- Fix: v79.105: build a separate local list (_stp_fields_iter = list(_stp_field_order)), insert interface fields into it, and iterate that — the closure name is never rebound.
### learning_needs_real_values — Learn — empty-form crawls capture little
- Symptom: Deep-learn showed Document Type 10, Data Map 1, Rule 1, Transport Profile 0, Biz Flow 0 fields.
- Root cause: The portal renders fields progressively as values are entered; an unfilled form has almost nothing to capture.
- Fix: v79.105: learn from REAL runs — every run's field_intelligence is auto-merged into the learned schema after each task, and a '📥 Learn from completed runs' button does it on demand. Run UHAUL_POASN once and the schema fills with what the run actually saw.
### multi_object_tasks — Profile Runner — repeated objects
- Symptom: Only the FIRST of multiple data maps/TPs/doc types was created; static single-task assumptions.
- Root cause: None
- Fix: v79.106: normalization layer + list-aware add_task → one task per item with repeat_index. Both old and new input.json schemas supported.
### chat_cannot_execute — Chat interface
- Symptom: 'Create a Transport Profile' / 'create the Document Type' in chat failed — chat only explained, never executed.
- Root cause: None
- Fix: v79.106: intent router detects creation/run intents and starts the Profile Runner via the proven state machine, using the cached plan or building one from the selected customer's input.json.
### excel_roundtrip_parser — Configuration Manual Excel round-trip
- Symptom: App could generate repeated-object Excel but couldn't parse it back — user edits in the generated workbook were lost.
- Root cause: Parser only recognized old Dell sheet names.
- Fix: v79.108: dual-format parser — agent-generated numbered KV sheets → lists; legacy Dell manuals unchanged. Verified by real-parser acceptance tests including the edit-reflection case.
### fullchain_converter_list_crash — Excel→input.json full chain
- Symptom: AttributeError 'list' object has no attribute 'get' (repeated data_map) and TypeError list indices must be integers (repeated target TP) in _config_manual_to_input_json/_apply_direction_to_objects after the v79.108 parser returned lists.
- Root cause: Converter and direction-applier assumed dict sections.
- Fix: v79.109: coerced representative reads + per-item direction application; lists preserved end-to-end. Proven by full-chain acceptance test T13.
### rule_sheets_dedupe_drop — Excel round-trip — multiple rules
- Symptom: Second rule lost: generator produced 'Rule Configuration1' (openpyxl dedupe), parser only matched 'Rule Configuration( N)?'.
- Root cause: None
- Fix: v79.110: explicit numbered rule sheet names with a space; full-chain test T14 proves both rules + edits survive.
### output_dm_not_generated — Excel generation
- Symptom: objects.output_file_data_map present but no Output File Data Map sheets written.
- Root cause: None
- Fix: v79.110: numbered Output File Data Map sheets generated; round-trip tested (T15).
### bizflow_list_generator_crash — Excel generation
- Symptom: AttributeError 'list' object has no attribute 'get' when objects.biz_flow was a list.
- Root cause: None
- Fix: v79.110: generator coerces to the first flow; blocking validator errors when >1 biz_flow (explicit, not silent).
### bizflow_sheet_not_parsed — Excel round-trip — Biz Flow
- Symptom: Biz Flow details (business_flow_name, process_steps, routing, flow identifiers) lost in generate→parse chain; only direction-injected configure_source/targets survived.
- Root cause: None
- Fix: v79.111: Biz Flow added to _GEN_SHEET_MAP with a dedicated section+table parser (T17).
### rule_conditions_flattened — Excel round-trip — rule conditions
- Symptom: Conditions table flattened into junk keys ('#': 'Condition Type', '1': 'Field'); conditions.rows[] lost.
- Root cause: None
- Fix: v79.111: dedicated rule-sheet parser rebuilds conditions.rows[] + actions{} (T18).
### bf_mapping_subcfg_legend_mismatch — BizFlow > Process Steps > Mapping Transformer sub-configuration
- Symptom: UHAL-POASN live run filled 48/52: row 0 (Mapping Transformer) logged bf_step_sub_config_no_combos reason=sub-fieldset not found — its Document Type Version, Action, Target Document Type, and Rule(Version) combos were skipped. Enricher row worked.
- Root cause: Legend lookup used substring t.includes('mapping configuration') but the REAL portal legend is 'Mapping Transformer Configuration' (token in the middle) — never matches. Enricher matched only because its legend equals its keyword. Row 0 also raced the async render with no retry.
- Fix: v79.124: token matching (legend must contain ALL of ['mapping','configuration'] / ['enricher','configuration']) in all three searches (row-scoped, doc-wide fallback, NEW post-wait retry ~0.9s 'retry_after_wait'). Locked by T108 truth table.
### rule_cond_attr_name_unit_row1_missed — Profile Runner > Rule Configuration > Configure Routing > condition rows > Attribute Name/Unit
- Symptom: Live rule run: row 0 (Receiver) filled fully; row 1's Value=DELL filled but its Attribute Name/Unit=Sender never committed. Log showed cond_field_id_lookup found BOTH attr ids then rule_cond_section_error: TimeoutError with no attr_row1_result.
- Root cause: The whole condition-rows coroutine ran under manager._run(..., timeout=60). Row 0 settles + row-1 add-row poll timeout + the ~2.4s Attribute settle pushed past 60s, guillotining row 1's attribute select mid-flight. The per-row try/except never fired because the timeout was on the OUTER coroutine.
- Fix: v79.136: (1) section budget 60→120s; (2) each per-row Attribute Name/Unit select wrapped in asyncio.wait_for(timeout=12) so it can't hang and always logs an outcome; (3) rows that fail to commit their attribute are tracked in _attr_pending and a POST-SECTION backfill re-fills them once all rows exist and the dialog is settled (attr_backfill_start/result). Locked by T199.

## Reference Profiles
| Profile | Customer | Standard | Tx | Direction | Mode |
|---|---|---|---|---|---|
| BLUMM_USA_PC_855_ANS_MAPPING_OB | BLUMM_USA | X12 | 855 | Outbound | Translation |
| DOUGLAS_STEWART_PC_855_ANS_MAPPING_OB | DOUGLAS_STEWART | X12 | 855 | Outbound | Translation |
| TECHDATA_UK_PC_855_ANS_MAPPING_OB | TECHDATA_UK | X12 | 855 | Outbound | Translation |
| U-HAUL_PC_855_ANS_MAPPING_OB | U-HAUL | X12 | 855 | Outbound | Translation |
| U-HAUL_PC_856_ANS_MAPPING_OB | U-HAUL | X12 | 856 | Outbound | Translation |
| U-HAUL_PC_855_ANS_MAPPING_OB | U-HAUL | X12 | 855 | Outbound | Translation |
| ATEA_SWEDEN_CO_PC_ORDERS_PREMIER_MAPPING_IB | ATEA_SWEDEN_CO | cXML | ORDERS | Inbound | Translation |
| ALSTERARBEIT_PC_CATALOG_PREMIER_PASSTHROUGH_IB | ALSTERARBEIT | B2BXML | CATALOG | Inbound | Passthrough |
| ATEA_NORWAY_PC_CATALOG_PREMIER_PASSTHROUGH_IB | ATEA_NORWAY | B2BXML | CATALOG | Inbound | Passthrough |
| ATEA_SWEDEN_PC_ORDERS_PREMIER_PASSTHROUGH_IB | ATEA_SWEDEN | cXML | ORDERS | Inbound | Passthrough |
| ATEA_SWEDEN_PC_CATALOG_PREMIER_PASSTHROUGH_OB | ATEA_SWEDEN | B2BXML | CATALOG | Outbound | Passthrough |
| GOLD_GROUP_PC_WELCOME_PREMIER_PASSTHROUGH_OB | GOLD_GROUP | XML | WELCOME | Outbound | Passthrough |
| KOHLER_IBM_STERLING_PC_832_PREMIER_PASSTHROUGH_OB | KOHLER_IBM_STERLING | CSV | 832 | Outbound | Passthrough |
| WILLMOTT_DIXON_PC_CATALOG_BHC_PASSTHROUGH_OB | WILLMOTT_DIXON | B2BXML | CATALOG | Outbound | Passthrough |
| WILLMOTT_DIXON_PC_CATALOG_BHC_PASSTHROUGH_IB | WILLMOTT_DIXON | B2BXML | CATALOG | Inbound | Passthrough |
| WINONA_HEALTH_PC_855_ANS_MAPPING_OB | WINONA_HEALTH | EDI | 855 | Outbound | Translation |

## Package Files
- `HIP_Unified_Deep_KB.html` — primary readable KB
- `HIP_Unified_Deep_KB.json` — machine-readable KB
- `HIP_Unified_Knowledge_Graph.json` — full graph
- `HIP_Unified_Knowledge_Graph.graphml` — graph-tool/Gephi import
- `knowledge_graph_nodes.csv`, `knowledge_graph_edges.csv`
- `source_inventory.csv`, `report_catalog.json`
- `TWO_RUN_EVIDENCE_REVIEW.json`, `TWO_RUN_EVIDENCE_REVIEW.md`
- `run_phase_verdicts.csv`, `selector_drift_summary.csv`, `run_artifact_hashes.csv`
