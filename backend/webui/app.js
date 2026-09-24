const $ = (id) => document.getElementById(id);
const state = { sections: [], runtime: null, process: null, preflight: null, liveRuntimeCertification: null, liveReadiness: null, descriptionPlan: null, runs: [], missionTrace: null, agentLiveView: null, agentLiveViewScreenshotUrl: "", worldModel: null, humanAssistance: null, humanSelectedControl: null, modelTests: { text:null, vision:null }, timer: null };

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!response.ok) throw new Error(data.detail || data.error || `${response.status} ${response.statusText}`);
  return data;
}

function toast(message, kind = "good") {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast ${kind}`;
  setTimeout(() => el.classList.add("hidden"), 3600);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (m) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" }[m]));
}

function table(rows, columns) {
  if (!rows?.length) return '<div class="empty">No rows available.</div>';
  return `<table class="table"><thead><tr>${columns.map(c=>`<th>${esc(c.label)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map(c=>`<td>${c.render ? c.render(row) : esc(row[c.key])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}


function renderModelTest(kind, result = null, pending = false) {
  const prefix = kind === "text" ? "textModel" : "visionModel";
  const badge = $(`${prefix}State`);
  const details = $(`${prefix}Details`);
  if (!badge || !details) return;
  if (pending) {
    badge.textContent = "Testing…"; badge.className = "badge info";
    details.innerHTML = '<b>Status</b><span>Calling the configured Dell AIA deployment…</span>';
    return;
  }
  if (!result) {
    badge.textContent = "Not tested"; badge.className = "badge neutral";
    return;
  }
  badge.textContent = result.pass ? "Available" : "Unavailable";
  badge.className = `badge ${result.pass ? "good" : "bad"}`;
  const verified = kind === "vision" ? result.image_understanding_verified : (result.capability_verified ?? result.response_received ?? result.expected_marker_seen);
  const contractWarning = kind === "text" ? (result.contract_warning || "") : "";
  details.innerHTML = `
    <b>Provider</b><span>${esc(result.provider || "Dell AIA GenAI Gateway")}</span>
    <b>Model</b><span>${esc(result.model || "—")}</span>
    <b>Latency</b><span>${esc(result.latency_ms ?? "—")} ms</span>
    <b>Endpoint configured</b><span>${result.endpoint_configured ? "Yes" : "No"}</span>
    ${Array.isArray(result.env_files_loaded) ? `<b>.env loaded</b><span>${result.env_files_loaded.length ? "Yes" : "No"}</span>` : ""}
    <b>Capability verified</b><span>${verified ? "Yes" : "No"}</span>
    <b>Result</b><span>${esc(result.status || "—")}</span>
    ${result.output_token_cap ? `<b>Output token cap</b><span>${esc(result.output_token_cap)}</span>` : ""}
    ${kind === "text" ? `<b>Synthetic marker</b><span>${result.expected_marker_seen ? "Matched" : "Not exact"}</span>` : ""}
    ${result.response_preview ? `<b>Model response</b><span class="mono">${esc(result.response_preview)}</span>` : ""}
    ${contractWarning ? `<b>Note</b><span>${esc(contractWarning)}</span>` : ""}
    ${result.error ? `<b>Error</b><span>${esc(result.error_kind || "model_error")}: ${esc(result.error)}</span>` : ""}`;
}

async function testModelAvailability(kind) {
  const button = $(kind === "text" ? "testTextModelBtn" : "testVisionModelBtn");
  if (button) button.disabled = true;
  renderModelTest(kind, null, true);
  try {
    const result = await api(`/api/models/${kind}/test`, {
      method:"POST",
      body:JSON.stringify({ config: $("configPath").value.trim() || "config.yaml" }),
    });
    state.modelTests[kind] = result;
    renderModelTest(kind, result, false);
    toast(`${kind === "text" ? "Text" : "Vision"} model ${result.pass ? "available" : "unavailable"}`, result.pass ? "good" : "bad");
    return result;
  } catch (error) {
    const result = { pass:false, status:"request_failed", error_kind:"backend_request", error:error.message };
    state.modelTests[kind] = result; renderModelTest(kind, result, false); toast(error.message, "bad");
    return result;
  } finally { if (button) button.disabled = false; }
}

function currentSection() { return $("sectionSelect").value || "all"; }
function selectedSpec() { return state.sections.find(s => s.id === currentSection()) || state.sections.at(-1) || { id:"all", label:"All HIP sections", phases:[] }; }

function missionPayload() {
  return {
    section: currentSection(),
    witness_mode: !!$("liveWitnessMode")?.checked,
    description: $("missionDescription").value.trim(),
    use_description_trigger: $("useDescriptionTrigger").checked,
    config: $("configPath").value.trim() || "config.yaml",
    input_json: $("inputPath").value.trim(),
    runs_dir: $("runsDir").value.trim() || "./runs",
    golden_screenshot_dir: $("goldenDir").value.trim(),
    upload_assets_dir: $("uploadsDir").value.trim(),
    api_mode: $("apiMode").value,
    write_heavy_evidence: $("heavyEvidence").checked,
    allow_api_mutation: $("allowApiMutation").checked,
    resume_run: $("resumeRun").value.trim(),
    until_complete: $("untilComplete").checked,
    readiness_token: state.liveReadiness?.token || "",
  };
}

function descriptionRequest() {
  const p = missionPayload();
  return {
    description:p.description, config:p.config, input_json:p.input_json,
    golden_screenshot_dir:p.golden_screenshot_dir, upload_assets_dir:p.upload_assets_dir,
  };
}

async function planDescription({silent=false}={}) {
  const description = $("missionDescription").value.trim();
  if (!description) { if(!silent) toast("Enter a mission description first", "bad"); return null; }
  const data = await api("/api/mission/description-plan", { method:"POST", body:JSON.stringify(descriptionRequest()) });
  state.descriptionPlan = data.description_plan || null;
  state.preflight = data;
  const spec = data.section || {};
  if (spec.id && state.sections.some(s=>s.id===spec.id)) $("sectionSelect").value = spec.id;
  $("useDescriptionTrigger").checked = true;
  renderScope(); renderPreflight();
  if(!silent) toast(`Description mapped to ${spec.label || (spec.phases||[]).join(", ")}`);
  return data;
}

async function loadSections() {
  const data = await api("/api/sections");
  state.sections = data.sections || [];
  const select = $("sectionSelect");
  select.innerHTML = state.sections.map(s => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join("");
  select.value = "all";
  renderScope();
}

function renderScope() {
  const triggered = $("useDescriptionTrigger")?.checked && state.descriptionPlan?.section;
  const spec = triggered ? state.descriptionPlan.section : selectedSpec();
  $("sectionNote").textContent = spec.dependency_note || (triggered ? "Description-selected deterministic HIP skills" : "");
  $("scopeMetric").textContent = spec.id === "all" ? "All sections" : String(spec.label || "Description-selected").replace(" only", "");
  $("phaseMetric").textContent = `${spec.phase_count || spec.phases?.length || 0} phase(s)`;
  const witness = !!$("liveWitnessMode")?.checked;
  if (witness) $("startBtn").textContent = spec.id === "all" ? "Start live witness" : "Run selected live witness";
  else $("startBtn").textContent = spec.id === "all" ? "Start full mission" : (triggered ? "Run description-triggered mission" : "Run selected section only");
}

function syncWitnessMode() {
  const witness = !!$("liveWitnessMode")?.checked;
  if (witness) {
    $("apiMode").value = "capture";
    $("allowApiMutation").checked = false;
  }
  $("allowApiMutation").disabled = witness;
  // Witness uses the completion-first bounded controller; avoid accidentally
  // turning the first live smoke into an unbounded recovery run.
  if (witness) $("untilComplete").checked = false;
  renderScope();
}

function invalidateLiveReadiness(reason = "Mission inputs changed") {
  state.liveReadiness = null;
  const badge=$("liveReadinessState"), details=$("liveReadinessSummary");
  if (badge) { badge.textContent="Not run"; badge.className="badge neutral"; }
  if (details) details.innerHTML=`<div class="empty">${esc(reason)}. Run Live GO/NO-GO before starting.</div>`;
  updateButtons();
}


function renderLiveRuntimeCertification(result = null, pending = false) {
  const badge=$("liveRuntimeCertState"), summary=$("liveRuntimeCertSummary"), tableWrap=$("liveRuntimeCertChecks");
  if (!badge || !summary || !tableWrap) return;
  if (pending) {
    badge.textContent="Checking…"; badge.className="badge info";
    summary.innerHTML='<div class="empty">Opening the real headed browser/runtime. Complete Dell SSO in the opened window if requested. No HIP mutation controls are authorized.</div>';
    tableWrap.innerHTML=""; return;
  }
  if (!result) {
    badge.textContent="Not run"; badge.className="badge neutral"; tableWrap.innerHTML=""; return;
  }
  badge.textContent=result.decision || (result.pass?"GO":"NO-GO"); badge.className=`badge ${result.pass?"good":"bad"}`;
  summary.innerHTML=`<div class="kv"><b>Decision</b><span>${esc(result.decision||"—")}</span><b>Certificate</b><span>${esc(result.certificate_id||"—")}</span><b>Blockers</b><span>${esc(result.blocker_count||0)}</span><b>Warnings</b><span>${esc(result.warning_count||0)}</span><b>Expires</b><span>${result.expires_at_epoch?new Date(result.expires_at_epoch*1000).toLocaleString():"—"}</span><b>Contract</b><span>${esc(result.non_mutating_contract||"")}</span></div>`;
  tableWrap.innerHTML=table(result.checks||[], [
    {key:"label",label:"Live runtime check"},
    {key:"pass",label:"Status",render:r=>r.pass?'<span class="badge good">PASS</span>':`<span class="badge ${r.severity==="warning"?"info":"bad"}">${r.severity==="warning"?"WARN":"BLOCK"}</span>`},
    {key:"detail",label:"Detail"},
  ]);
}

async function runLiveRuntimeCertification() {
  const button=$("liveRuntimeCertBtn"); if(button) button.disabled=true;
  renderLiveRuntimeCertification(null,true);
  try {
    const result=await api("/api/mission/live-runtime-certification", {method:"POST", body:JSON.stringify({
      config:$("configPath").value.trim()||"config.yaml",
      runs_dir:$("runsDir").value.trim()||"./runs",
      target_url:"",
      ttl_seconds:0,
      require_pyautogui_mcp:false,
    })});
    state.liveRuntimeCertification=result;
    renderLiveRuntimeCertification(result,false);
    invalidateLiveReadiness("Windows runtime certification changed");
    toast(result.pass?"Windows runtime certified — now run Live GO/NO-GO":"Windows runtime NO-GO — fix blockers and retry", result.pass?"good":"bad");
    return result;
  } catch(error) {
    const result={pass:false,decision:"NO_GO",checks:[],blocker_count:1,error:error.message};
    state.liveRuntimeCertification=result; renderLiveRuntimeCertification(result,false); toast(error.message,"bad"); return result;
  } finally { if(button) button.disabled=false; }
}

function renderLiveReadiness(result = null, pending = false) {
  const badge=$("liveReadinessState"), summary=$("liveReadinessSummary"), tableWrap=$("liveReadinessChecks");
  if (!badge || !summary || !tableWrap) return;
  if (pending) {
    badge.textContent="Checking…"; badge.className="badge info";
    summary.innerHTML='<div class="empty">Launching non-invasive browser/MCP probes and testing Dell AIA text + vision models…</div>';
    tableWrap.innerHTML=""; return;
  }
  if (!result) {
    badge.textContent="Not run"; badge.className="badge neutral";
    summary.innerHTML='<div class="empty">Run this gate immediately before a real mission.</div>'; tableWrap.innerHTML=""; return;
  }
  badge.textContent=result.decision || (result.pass?"GO":"NO-GO"); badge.className=`badge ${result.pass?"good":"bad"}`;
  summary.innerHTML=`<div class="kv"><b>Decision</b><span>${esc(result.decision||"—")}</span><b>Profile</b><span>${esc(result.execution_profile||"standard")}</span><b>Blockers</b><span>${esc(result.blocker_count||0)}</span><b>Warnings</b><span>${esc(result.warning_count||0)}</span><b>Receipt</b><span>${result.pass?`Valid for ${esc(result.expires_in_seconds||0)} seconds`:`Not issued`}</span><b>Execution contract</b><span>${esc(result.execution_contract||"")}</span>${result.witness_guarantee?`<b>Witness guarantee</b><span>${esc(result.witness_guarantee)}</span>`:""}</div>`;
  tableWrap.innerHTML=table(result.checks||[], [
    {key:"label",label:"Live check"},
    {key:"pass",label:"Status",render:r=>r.pass?'<span class="badge good">PASS</span>':`<span class="badge ${r.severity==="warning"?"info":"bad"}">${r.severity==="warning"?"WARN":"BLOCK"}</span>`},
    {key:"detail",label:"Detail"},
  ]);
}

async function runLiveReadiness({silent=false}={}) {
  if (!state.preflight) await runPreflight(true);
  renderLiveReadiness(null, true);
  const button=$("liveReadinessBtn"); if(button) button.disabled=true;
  try {
    const p=missionPayload();
    const result=await api("/api/mission/live-readiness", {method:"POST", body:JSON.stringify({
      section:p.section, witness_mode:p.witness_mode, description:p.description, use_description_trigger:p.use_description_trigger,
      config:p.config, input_json:p.input_json, runs_dir:p.runs_dir, golden_screenshot_dir:p.golden_screenshot_dir,
      upload_assets_dir:p.upload_assets_dir, api_mode:p.api_mode,
    })});
    state.liveReadiness=result;
    state.modelTests.text=(result.checks||[]).find(x=>x.id==="text_model")?.evidence || state.modelTests.text;
    state.modelTests.vision=(result.checks||[]).find(x=>x.id==="vision_model")?.evidence || state.modelTests.vision;
    if(state.modelTests.text) renderModelTest("text", state.modelTests.text, false);
    if(state.modelTests.vision) renderModelTest("vision", state.modelTests.vision, false);
    renderLiveReadiness(result, false); updateButtons();
    if(!silent) toast(result.pass?"Live readiness GO — mission may start":"Live readiness NO-GO — fix blockers first", result.pass?"good":"bad");
    return result;
  } catch(error) {
    state.liveReadiness={pass:false,decision:"NO_GO",checks:[],blocker_count:1,error:error.message};
    renderLiveReadiness(state.liveReadiness,false); updateButtons(); if(!silent) toast(error.message,"bad"); return state.liveReadiness;
  } finally { if(button) button.disabled=false; }
}

async function runPreflight(silent = false) {
  try {
    const p = missionPayload();
    if (p.use_description_trigger && p.description) {
      state.preflight = await api("/api/mission/description-plan", { method:"POST", body:JSON.stringify(descriptionRequest()) });
      state.descriptionPlan = state.preflight.description_plan || null;
    } else {
      state.preflight = await api("/api/mission/preflight", { method:"POST", body:JSON.stringify({
        section:p.section, config:p.config, input_json:p.input_json, runs_dir:p.runs_dir, golden_screenshot_dir:p.golden_screenshot_dir, upload_assets_dir:p.upload_assets_dir,
      }) });
      state.descriptionPlan = null;
    }
    renderPreflight();
    if (state.liveReadiness) invalidateLiveReadiness("Static preflight reran");
    if (!silent) toast(state.preflight.pass ? "Preflight passed" : "Preflight has blocking issues", state.preflight.pass ? "good" : "bad");
  } catch (error) {
    state.preflight = null;
    renderPreflight(error.message);
    if (!silent) toast(error.message, "bad");
  }
}

function renderPreflight(error = "") {
  const p = state.preflight;
  if (!p) {
    $("preflightMetric").textContent = "Failed";
    $("preflightState").textContent = "Error";
    $("preflightState").className = "badge bad";
    $("preflightSummary").innerHTML = `<div class="empty">${esc(error || "Preflight not available")}</div>`;
    return;
  }
  const pass = !!p.pass && !!p.autogen?.pass;
  $("preflightMetric").textContent = pass ? "Ready" : "Blocked";
  $("preflightState").textContent = pass ? "Ready" : "Blocked";
  $("preflightState").className = `badge ${pass ? "good" : "bad"}`;
  $("inputMetric").textContent = p.input_contract?.section_scoped ? "Section-scoped input" : "Input validated";
  const issues = p.issues || [];
  const autogen = p.autogen || {};
  $("preflightSummary").innerHTML = `
    <div class="kv">
      <b>Input contract</b><span>${p.input_contract?.pass ? "PASS" : "FAIL"}</span>
      <b>Golden screenshots</b><span>${p.golden_screenshots_pass ? "Ready" : "Missing"}</span>
      <b>Upload assets</b><span>${p.upload_assets_pass ? "Ready" : "Missing"}</span>
      <b>AutoGen</b><span>${autogen.pass ? "0.7.5 Ready" : esc(autogen.reason || "Not ready")}</span>
      <b>Browser</b><span>${esc(state.runtime?.browser?.primary_name || state.runtime?.browser?.channel || "Chromium")} (same persistent CDP session)</span>
      <b>Vision model</b><span>${state.runtime?.vision_runtime?.enabled ? (state.runtime?.vision_runtime?.explicit_model_candidates?.length ? `Configured: ${esc(state.runtime.vision_runtime.explicit_model_candidates.join(", "))}` : "Enabled; model resolved/probed at runtime") : "Disabled"}</span>
      <b>5-minute loading watchdog</b><span>${state.runtime?.vision_runtime?.use_for_loading_watchdog ? `Vision-confirmed refresh after ${esc(state.runtime.vision_runtime.loading_refresh_after_seconds || 300)}s` : "Disabled"}</span>
      <b>PyAutoGUI fallback</b><span>${state.runtime?.pyautogui?.available ? "Ready (last resort)" : (state.runtime?.pyautogui?.enabled ? esc(state.runtime?.pyautogui?.reason || "Unavailable on this host") : "Disabled")}</span>
      <b>AutoWebGLM recovery</b><span>${state.runtime?.autowebglm?.enabled ? `${esc(state.runtime.autowebglm.official_action_count || 10)}-action protocol (${state.runtime?.autowebglm?.native_model_configured ? "native wrapper" : "Dell AIA planner"})` : "Disabled"}</span>
      <b>LangChain browser toolkit</b><span>${state.runtime?.langchain_browser_toolkit?.enabled ? "Read-only same-browser recovery" : "Disabled"}</span>
      <b>Version source</b><span>${esc(Object.entries(autogen.version_sources || {}).map(([k,v])=>`${k}: ${v}`).join(", ") || "—")}</span>
      <b>Blocking issues</b><span>${issues.length}</span>
    </div>${issues.length ? `<pre class="json-output">${esc(JSON.stringify(issues,null,2))}</pre>` : ""}`;
  const skill = p.skill_vetting || {};
  $("skillVetting").innerHTML = table(skill.skills || [], [
    {key:"phase",label:"Phase"},{key:"label",label:"Expert skill"},{key:"pass",label:"Vetted",render:r=>r.pass?'<span class="badge good">PASS</span>':'<span class="badge bad">FAIL</span>'},
    {key:"deterministic_contract",label:"Deterministic contract"},
  ]);
  const budget = p.context_budget || {};
  $("contextBudget").innerHTML = `<b>Policy</b><span>${esc(budget.policy || "—")}</span><b>Phases</b><span>${esc((budget.selected_phases||[]).join(", "))}</span><b>Input branches</b><span>${esc((budget.selected_input_keys||[]).join(", ") || "—")}</span><b>Capabilities loaded</b><span>${esc(budget.capability_count ?? 0)}</span><b>Context chars</b><span>${esc(budget.serialized_chars_before_limit ?? 0)} / ${esc(budget.max_serialized_chars ?? 0)}</span>`;
  $("phaseCoverage").innerHTML = table(p.phase_rows || [], [
    {key:"phase",label:"Phase"},{key:"pass",label:"Pass",render:r=>r.pass?'<span class="badge good">PASS</span>':'<span class="badge bad">FAIL</span>'},
    {key:"leaf_coverage",label:"Leaf coverage %"},{key:"mapped_paths",label:"Mapped"},{key:"accounted_nonmutable",label:"Nonmutable"},{key:"unmapped",label:"Unmapped"},
  ]);
  $("rowPlan").innerHTML = table(p.repeatable_row_plan || [], [
    {key:"phase",label:"Phase"},{key:"section",label:"Repeatable section"},{key:"json_rows",label:"JSON rows"},{key:"plus_clicks_from_one_initial_row",label:"+ clicks"},
  ]);
  updateButtons();
}

async function uploadInput(file) {
  const content = await file.text();
  const result = await api("/api/input/upload", { method:"POST", body:JSON.stringify({ filename:file.name, content }) });
  $("inputPath").value = result.path;
  toast(`Using ${result.filename}`);
  await runPreflight(true);
}

async function startMission() {
  try {
    if (!state.preflight) await runPreflight(true);
    if (!state.preflight?.pass || !state.preflight?.autogen?.pass || !state.preflight?.skill_vetting?.pass) throw new Error("Mission is blocked by input/AutoGen/expert-skill vetting. Fix the displayed issues first.");
    if (!state.liveReadiness?.pass) await runLiveReadiness({silent:true});
    if (!state.liveReadiness?.pass || !state.liveReadiness?.token) throw new Error("Live GO/NO-GO readiness is blocked. Fix the live blockers before starting.");
    const payload = missionPayload();
    if (payload.witness_mode && (payload.api_mode === "write" || payload.allow_api_mutation)) throw new Error("Live witness mode is strictly non-mutating. Disable API mutation/write mode.");
    if (payload.api_mode === "write" && !payload.allow_api_mutation) throw new Error("API write mode requires explicit Allow API mutation.");
    const result = await api("/api/mission/start", { method:"POST", body:JSON.stringify(payload) });
    toast(`Mission started (PID ${result.pid})`);
    await refreshStatus();
    showTab("mission");
  } catch (error) { toast(error.message, "bad"); }
}

async function processAction(action) {
  try {
    const result = await api(`/api/discovery/${action}`, { method:"POST", body:"{}" });
    toast(`${action}: ${result.status || "ok"}`);
    await refreshStatus();
  } catch (error) { toast(error.message, "bad"); }
}

function traceStatusClass(status) {
  const s=String(status||"").toLowerCase();
  if (["complete","completed","resumed","pass"].includes(s)) return "good";
  if (["blocked","failed","error"].includes(s)) return "bad";
  if (["running","in_progress"].includes(s)) return "info";
  return "neutral";
}

function compactTraceItems(rows, kind) {
  const items=(rows||[]).slice(-8);
  if (!items.length) return '<div class="empty">No '+esc(kind)+' evidence yet.</div>';
  return `<ul class="trace-list">${items.map(row=>{
    const execution=row.execution||{};
    const details=row.details||{};
    const exec=execution.actual_executor||row.backend||"";
    const planner=execution.planner||"";
    const fallback=execution.fallback_reason||"";
    const semanticId=execution.semantic_control_id||details.semantic_control_id||"";
    const semanticConfidence=execution.semantic_confidence ?? details.confidence ?? details.effect_confidence;
    const semanticStatus=execution.semantic_gate_status||details.status||"";
    const semanticEffect=execution.semantic_effect_type||details.effect_type||"";
    const semanticText=semanticId ? ` • semantic: ${esc(semanticId)}${semanticConfidence!==null&&semanticConfidence!==undefined?` @ ${Number(semanticConfidence).toFixed(2)}`:""}${semanticStatus?` (${esc(semanticStatus)})`:""}${semanticEffect?` → ${esc(semanticEffect)}`:""}` : "";
    const value=kind==="filled" ? ` = <strong>${esc(row.value||"")}</strong>` : "";
    const summary=row.summary||row.target||row.control||row.type||"observed";
    return `<li>${esc(summary)}${value}${planner?` • planner: ${esc(planner)}`:""}${exec?` • <span class="trace-executor">executor: ${esc(exec)}</span>`:""}${semanticText}${fallback?` • <span class="trace-fallback">fallback: ${esc(fallback)}</span>`:""}</li>`;
  }).join("")}</ul>`;
}


function scoreText(value) {
  if (value === null || value === undefined || value === "") return "—";
  const num=Number(value); return Number.isFinite(num) ? num.toFixed(2) : esc(value);
}

function renderAdaptiveMetric(summary) {
  const health=summary?.learning_health||{};
  const score=Math.max(0,Math.min(100,Number(health.learning_score||0)));
  if ($("adaptiveMetric")) $("adaptiveMetric").textContent=`${Math.round(score)}%`;
  if ($("adaptiveDetail")) $("adaptiveDetail").textContent=`${health.fresh_knowledge_count||0} fresh • ${health.stale_knowledge_count||0} stale • ${health.drift_suspect_count||0} drift`;
  if ($("agentLearningHealth")) $("agentLearningHealth").textContent=`${Math.round(score)}%`;
  if ($("agentLearningProgress")) $("agentLearningProgress").style.width=`${score}%`;
  if ($("agentStaleKnowledge")) $("agentStaleKnowledge").textContent=String(health.stale_knowledge_count||0);
  if ($("agentDriftKnowledge")) $("agentDriftKnowledge").textContent=String(health.drift_suspect_count||0);
  if ($("agentFreshKnowledge")) $("agentFreshKnowledge").textContent=String(health.fresh_knowledge_count||0);
}

function visualStage(cur) {
  const stage=String(cur?.visual_overlay?.stage||"").toLowerCase();
  const event=String(cur?.event||"").toLowerCase();
  if (stage==="failed" || event.includes("failed") || cur?.verification?.status==="failed") return "failed";
  if (stage==="verified" || event.includes("verified") || cur?.verification?.status==="verified") return "verified";
  if (stage==="acting" || event.includes("acting") || cur?.execution?.status==="acting") return "acting";
  if (stage==="selected" || event.includes("selected") || event.includes("planner")) return "selecting";
  return "waiting";
}

function renderAgentLiveView(payload) {
  const panel=$("agentLiveViewPanel"), badge=$("agentLiveViewState"), image=$("agentLiveScreenshot"), imageEmpty=$("agentLiveScreenshotEmpty");
  const intent=$("agentLiveIntent"), surface=$("agentLiveSurface"), selected=$("agentLiveSelected"), candidates=$("agentLiveCandidates"), options=$("agentLiveOptions"), memory=$("agentLiveMemory"), memoryHints=$("agentLiveMemoryHints"), planner=$("agentLivePlanner"), execution=$("agentLiveExecution"), verification=$("agentLiveVerification");
  const banner=$("agentVisualStateBanner"), counters=$("agentOverlayCounters"), historyEl=$("agentDecisionHistory");
  if (!panel || !badge) return;
  const view=payload?.live_view||{}; const cur=view?.current||{};
  state.agentLiveView=view; state.agentLiveViewScreenshotUrl=payload?.screenshot_url||"";
  const globalSummary=state.worldModel?.summary||{};
  const currentSummary=cur?.website_memory?.summary||{};
  renderAdaptiveMetric(Object.keys(currentSummary).length?currentSummary:globalSummary);
  if (!payload?.found || !Object.keys(cur).length) {
    badge.textContent="Waiting"; badge.className="badge neutral";
    if (banner) { banner.className="visual-state-banner waiting"; banner.innerHTML='<strong>WAITING</strong><span>Memory proposes; the live page authorizes every action.</span>'; }
    if (counters) counters.innerHTML=['seen','candidate','selected','acting','verified','revealed','failed','foreground'].map(k=>`<div class="visual-counter"><b>0</b><span>${esc(k)}</span></div>`).join("");
    if (intent) intent.innerHTML='<div class="empty">No semantic target has been selected yet.</div>';
    if (surface) surface.innerHTML=""; if(selected) selected.innerHTML=""; if(candidates) candidates.innerHTML='<div class="empty">Candidate ranking will appear before each action.</div>';
    if(options) options.innerHTML='<div class="empty">Dropdown options will appear when a choice control is selected.</div>';
    if(memory) memory.innerHTML=""; if(memoryHints) memoryHints.innerHTML='<div class="empty">Verified website memory will appear after successful actions.</div>';
    if(planner) planner.innerHTML=""; if(execution) execution.innerHTML=""; if(verification) verification.innerHTML="";
    if(historyEl) historyEl.innerHTML='<div class="empty">Decision history will appear during a mission.</div>';
    if (image) image.classList.add("hidden"); if (imageEmpty) imageEmpty.classList.remove("hidden");
    return;
  }
  const event=String(cur.event||"observing"); const stage=visualStage(cur);
  badge.textContent=stage==="failed"?"Failed":stage==="verified"?"Verified":stage==="acting"?"Acting":"Selecting";
  badge.className=`badge ${stage==="failed"?"bad":stage==="verified"?"good":"info"}`;
  if (banner) {
    const copy={selecting:"Live candidates ranked; selected target is being re-proven.",acting:"Final semantic revalidation passed; governed physical action is dispatching.",verified:"Independent effect verification passed; verified semantics may strengthen memory.",failed:"Verification failed; negative evidence lowers confidence and may trigger drift demotion."}[stage]||"Memory proposes; the live page authorizes every action.";
    banner.className=`visual-state-banner ${stage}`; banner.innerHTML=`<strong>${esc(stage.toUpperCase())}</strong><span>${esc(copy)}</span>`;
  }
  const overlay=cur.visual_overlay||{}, counts=overlay.counts||{};
  if(counters) counters.innerHTML=['seen','candidate','selected','acting','verified','revealed','failed','foreground'].map(k=>`<div class="visual-counter"><b>${esc(counts[k]??0)}</b><span>${esc(k)}</span></div>`).join("");
  if (payload?.screenshot_url && image) {
    image.src=`${payload.screenshot_url}${payload.screenshot_url.includes("?")?"&":"?"}_ts=${Date.now()}`;
    image.classList.remove("hidden"); if(imageEmpty) imageEmpty.classList.add("hidden");
  } else { if(image) image.classList.add("hidden"); if(imageEmpty) imageEmpty.classList.remove("hidden"); }
  const sc=cur.selected_control||{}, as=cur.active_surface||{};
  if(intent) intent.innerHTML=`<b>Intent</b><span>${esc(cur.intent||cur.action||"—")}</span><b>Phase</b><span>${esc(cur.phase||"—")}</span><b>Expected</b><span>${esc(cur.expected_value||"—")}</span><b>Event</b><span>${esc(event)}</span><b>Overlay stage</b><span>${esc(overlay.stage||"—")}</span>`;
  if(surface) surface.innerHTML=`<b>Active surface</b><span>${esc(as.label||"—")}</span><b>Role</b><span>${esc(as.role||as.tag||"—")}</span><b>Selected tab</b><span>${esc((as.selected_tabs||[]).join(", ")||"—")}</span><b>Understanding confidence</b><span>${scoreText(as.understanding_confidence)}</span><b>Foreground-owned candidates</b><span>${overlay.foreground_owned_only===true?"Yes":"Current page"}</span>`;
  if(selected) selected.innerHTML=`<b>Selected control</b><span>${esc(sc.label||"—")}</span><b>Section</b><span>${esc(sc.section||"—")}</span><b>Role/type</b><span>${esc([sc.role,sc.control_type].filter(Boolean).join(" / ")||"—")}</span><b>Semantic ID</b><span class="mono-inline">${esc(sc.semantic_control_id||"—")}</span><b>Confidence</b><span>${scoreText(sc.confidence)}${sc.margin!==undefined&&sc.margin!==null?` • margin ${scoreText(sc.margin)}`:""}</span>`;
  const rows=(cur.candidate_ranking||[]);
  if(candidates) candidates.innerHTML=rows.length?table(rows,[
    {key:"rank",label:"#"},
    {key:"label",label:"Candidate / teach",render:r=>`${r.selected?'<span class="candidate-selected">SELECTED</span> ':''}<button class="teach-control-button" onclick="pickTeachControl('${encodeURIComponent(r.semantic_control_id||'')}','${encodeURIComponent(r.label||'')}','${encodeURIComponent(r.section||'')}','${encodeURIComponent(r.role||'')}')">${esc(r.label||r.semantic_control_id||"—")}</button>`},
    {key:"section",label:"Section"},{key:"role",label:"Role"},
    {key:"score",label:"Score",render:r=>scoreText(r.score)},
    {key:"rejection_reason",label:"Decision",render:r=>r.selected?'<span class="good-text">chosen</span>':`<span class="muted">${esc(r.rejection_reason||"lower rank")}</span>`},
  ]):'<div class="empty">No ranked candidates recorded for this action.</div>';
  const opts=cur.current_dropdown_options||[];
  if(options) options.innerHTML=opts.length?`<div class="option-chip-grid">${opts.map(o=>`<span class="option-chip">${esc(o)}</span>`).join("")}</div>`:'<div class="empty">No mounted dropdown/listbox options captured for the selected control.</div>';
  const wm=cur.website_memory||{}, ws=wm.summary||globalSummary||{}, wc=ws.controls||{}, wt=ws.transitions||{}, lh=ws.learning_health||{};
  renderAdaptiveMetric(ws);
  if(memory) memory.innerHTML=`<b>Status</b><span>${esc(wm.status||"new / no prior")}</span><b>Validated controls</b><span>${esc(wc.validated??0)}</span><b>Candidate controls</b><span>${esc(wc.candidate??0)}</span><b>Negative controls</b><span>${esc(wc.negative??0)}</span><b>Semantic states</b><span>${esc(ws.states??0)}</span><b>Validated transitions</b><span>${esc(wt.validated??0)}</span><b>Negative transitions</b><span>${esc(wt.negative??0)}</span><b>Effective confidence</b><span>${scoreText(lh.effective_confidence_average)}</span><b>Policy</b><span>Memory proposes; every action is re-proven live.</span>`;
  const mh=wm.hints||ws.recent_validated_hints||[];
  if(memoryHints) memoryHints.innerHTML=mh.length?table(mh,[
    {key:"trust",label:"Trust"},{key:"action_family",label:"Action"},
    {key:"control",label:"Remembered semantic control",render:r=>esc([r.control?.section,r.control?.label,r.control?.role].filter(Boolean).join(" → ")||"—")},
    {key:"effect_type",label:"Verified effect"},{key:"confidence",label:"Effective confidence",render:r=>scoreText(r.confidence)},
    {key:"age_days",label:"Age",render:r=>r.age_days===undefined?"—":`${esc(r.age_days)}d`},
    {key:"drift_suspect",label:"Drift",render:r=>r.drift_suspect?'<span class="bad-text">suspect</span>':'stable'},
    {key:"success_count",label:"Successes"},
  ]):'<div class="empty">No prior verified transition matched this action yet.</div>';
  const pl=cur.planner||{}; if(planner) planner.innerHTML=`<b>Framework</b><span>${esc(pl.framework||"AutoWebGLM")}</span><b>Aligned</b><span>${pl.aligned===true?"Yes":pl.aligned===false?"No":"Pending"}</span><b>Reason</b><span>${esc(pl.reason||"—")}</span>`;
  const ex=cur.execution||{}; if(execution) execution.innerHTML=`<b>Status</b><span>${esc(ex.status||"pending")}</span><b>Primary executor</b><span>${esc(ex.primary_executor||"—")}</span><b>Actual executor</b><span>${esc(ex.actual_executor||"—")}</span><b>Fallback</b><span>${esc(ex.fallback_reason||"—")}</span><b>Semantic revalidation</b><span>${esc(ex.semantic_revalidation||"—")}</span>`;
  const ve=cur.verification||{}; if(verification) verification.innerHTML=`<b>Status</b><span>${esc(ve.status||"pending")}</span><b>Effect</b><span>${esc(ve.semantic_effect_type||"—")}</span><b>Effect confidence</b><span>${scoreText(ve.semantic_effect_confidence)}</span><b>Exact commit</b><span>${ve.exact_value_commit_verified===true?"Yes":ve.exact_value_commit_verified===false?"No":"Pending"}</span><b>Observed</b><span>${esc(ve.observed_value||"—")}</span>${ve.error?`<b>Error</b><span class="bad-text">${esc(ve.error)}</span>`:""}`;
  const hist=(view.history||[]).slice(-8).reverse();
  if(historyEl) historyEl.innerHTML=hist.length?hist.map(h=>{
    const st=visualStage(h); const control=h.selected_control||{};
    const time=String(h.at||"").replace("T"," ").slice(0,19)||"—";
    return `<div class="decision-row ${esc(st)}"><time>${esc(time)}</time><span class="decision-event">${esc(h.event||"event")}</span><span class="decision-label">${esc(control.label||h.intent||h.action||"—")}</span><span class="decision-state">${esc(st)}</span></div>`;
  }).join(""):'<div class="empty">Decision history will appear during a mission.</div>';
}

function renderMissionTrace(payload) {
  const wrap=$("missionTraceSteps"), badge=$("missionTraceState"), summary=$("missionTraceSummary");
  if (!wrap || !badge || !summary) return;
  const trace=payload?.trace||payload||{};
  if (!trace || !Array.isArray(trace.steps)) {
    badge.textContent="Waiting"; badge.className="badge neutral";
    summary.textContent="No live mission trace yet."; wrap.innerHTML=""; return;
  }
  state.missionTrace=trace;
  badge.textContent=trace.status||"in_progress"; badge.className=`badge ${traceStatusClass(trace.status)}`;
  const runtime=trace.runtime_contract||{};
  summary.textContent=`Run ${trace.run_id||"—"} • ${trace.completed_step_ids?.length||0}/${trace.steps.length} complete • ${runtime.planner||"AutoWebGLM"} planner • ${runtime.primary_safe_action_executor||"PyAutoGUI MCP"} primary executor${runtime.semantic_understanding_enabled?" • semantic action gate ON":""}${runtime.autonomous_all_form_phases?" • autonomous/adaptive ALL PHASES":""}${runtime.playwright_mcp_available===false?" • MCP not yet attached":""}`;
  wrap.innerHTML=trace.steps.map(step=>{
    const status=String(step.status||"queued");
    const fills=step.filled||[], clicks=step.clicked||[], seen=[...(step.observed_controls||[]), ...(step.observed||[])];
    const verify=step.verification||{}, judge=step.judge||{}, handoff=step.handoff||{};
    const blocker=step.blocker||"";
    const missingFields=(judge.missing_fields||[]).slice(0,6);
    const judgeBlockDetail=judge.pass===false && missingFields.length
      ? `Judge missing: ${missingFields.map(x=>`${x.field||"field"}${x.reason?` (${x.reason})`:""}`).join("; ")}`
      : "";
    return `<div class="mission-step ${esc(status)}">
      <div class="mission-step-head"><div class="mission-step-title"><span class="mission-step-id">${esc(step.step_id||"")}</span><strong>${esc(step.label||step.phase||"")}</strong></div><span class="badge ${traceStatusClass(status)}">${esc(status)}</span></div>
      <div class="mission-step-grid">
        <div><b>Activity</b><span>${esc(step.current_activity||"Waiting")}</span></div>
        <div><b>Attempt</b><span>${esc(step.attempt||0)}</span></div>
        <div><b>Observed / Filled / Clicked</b><span>${seen.length} / ${fills.length} / ${clicks.length}</span></div>
        <div><b>Verification</b><span>${verify.pass===true?"Exact pass":verify.pass===false?"Failed":"Pending"}${judge.pass===true?" • judge pass":judge.pass===false?" • judge fail":""}${judge.multi_model_consensus_used?` • panel ${judge.multi_model_pass_votes||0}/${(judge.multi_model_pass_votes||0)+(judge.multi_model_fail_votes||0)} pass`:''}${judge.human_review_status?` • human ${esc(judge.human_verdict||judge.human_review_status)}`:''}</span></div>
        <div><b>Phase handoff</b><span>${handoff.status?`${esc(handoff.status)} → ${esc(handoff.to_step_id||"")}`:"Pending"}</span></div>
      </div>
      ${blocker?`<div class="help" style="color:#ff9dab"><strong>Blocker:</strong> ${esc(blocker)}</div>`:""}
      ${judgeBlockDetail?`<div class="help" style="color:#ffbf73"><strong>Judge evidence:</strong> ${esc(judgeBlockDetail)}${judge.text_model_status?` • text: ${esc(judge.text_model_status)}`:""}${judge.vision_model_status?` • vision: ${esc(judge.vision_model_status)}`:""}</div>`:""}
      <details><summary>What the agent observed / saw (${seen.length})</summary>${compactTraceItems(seen,"observed")}</details>
      <details><summary>What the agent filled (${fills.length})</summary>${compactTraceItems(fills,"filled")}</details>
      <details><summary>What the agent clicked / executed (${clicks.length})</summary>${compactTraceItems(clicks,"clicked")}</details>
    </div>`;
  }).join("");
}

async function refreshStatus() {
  try {
    const [runtime, process, runs, tracePayload, liveViewPayload, worldModelPayload] = await Promise.all([
      api(`/api/runtime/status?config=${encodeURIComponent($("configPath").value || "config.yaml")}`),
      api("/api/discovery/status"),
      api(`/api/runs?config=${encodeURIComponent($("configPath").value || "config.yaml")}&runs_dir=${encodeURIComponent($("runsDir").value || "./runs")}&limit=25`).catch(()=>({runs:[],count:0})),
      api(`/api/mission/trace?config=${encodeURIComponent($("configPath").value || "config.yaml")}&runs_dir=${encodeURIComponent($("runsDir").value || "./runs")}`).catch(()=>({found:false,trace:{}})),
      api(`/api/mission/live-view?config=${encodeURIComponent($("configPath").value || "config.yaml")}&runs_dir=${encodeURIComponent($("runsDir").value || "./runs")}`).catch(()=>({found:false,live_view:{},screenshot_url:""})),
      api(`/api/mission/world-model?config=${encodeURIComponent($("configPath").value || "config.yaml")}`).catch(()=>({found:false,summary:{}})),
    ]);
    state.runtime = runtime; state.process = process; state.runs = runs.runs || []; state.missionTrace = tracePayload.trace || null; state.worldModel = worldModelPayload || null;
    $("backendBadge").textContent = "Backend ready"; $("backendBadge").className = "badge good";
    const ag = runtime.autogen || {};
    $("autogenBadge").textContent = ag.pass ? "AutoGen 0.7.5" : "AutoGen blocked";
    $("autogenBadge").className = `badge ${ag.pass ? "good" : "bad"}`;
    const procText = process.paused ? "Paused" : process.running ? "Running" : "Stopped";
    $("processMetric").textContent = procText;
    $("pidMetric").textContent = process.pid ? `PID ${process.pid}` : "PID —";
    $("liveState").textContent = procText; $("liveState").className = `badge ${process.running ? (process.paused?"info":"good") : "neutral"}`;
    $("runsMetric").textContent = String(runs.count || state.runs.length);
    $("apiMetric").textContent = $("apiMode").value;
    const mf=runtime.mlflow||{};
    const si=runtime.skill_induction||{};
    const rp=runtime.replay_policy||{};
    const mp=runtime.model_portfolio||{};
    const ri=runtime.recursive_self_improvement||{};
    if ($("skillsMetric")) {
      $("skillsMetric").textContent = !si.enabled ? "Off" : String(si.validated_skill_count||0);
      $("skillsDetail").textContent = si.enabled ? `${si.skill_count||0} total • ${si.drift_suspect_count||0} drift • ${si.stale_skill_count||0} stale` : "skill induction disabled";
    }
    if ($("mlflowMetric")) {
      $("mlflowMetric").textContent = !mf.enabled ? "Off" : (mf.package_available ? "Async ready" : "Unavailable");
      $("mlflowDetail").textContent = mf.enabled ? `${mf.version||"MLflow"} • ${mf.experiment_name||"HIP Portal Agent"}` : "observability disabled";
    }
    if ($("policyMetric")) {
      $("policyMetric").textContent = !rp.enabled ? "Off" : `${rp.exploitation_policy_count||0}/${rp.policy_count||0}`;
      $("policyDetail").textContent = rp.enabled ? `${rp.episode_count||0} episodes • ${rp.hybrid_policy_count||0} hybrid • v${rp.active_policy_version||0}` : "replay policy disabled";
    }
    if ($("modelPortfolioMetric")) {
      const champs=mp.role_champions||{};
      $("modelPortfolioMetric").textContent = !mp.enabled ? "Off" : (champs.planning||champs.action_selection||"Learning");
      $("modelPortfolioDetail").textContent = mp.enabled ? `${(mp.text_models||[]).length} text • ${(mp.vision_models||[]).length} vision • cycle ${mp.cycle||0}` : "model portfolio disabled";
    }
    if ($("recursiveMetric")) {
      $("recursiveMetric").textContent = !ri.enabled ? "Off" : `Cycle ${ri.cycle||0}`;
      $("recursiveDetail").textContent = ri.enabled ? `best ${Number(ri.best_reward||0).toFixed(3)} • plateau ${ri.plateau_count||0}` : "recursive improvement disabled";
    }
    $("console").textContent = process.console_tail || "Waiting for mission output…";
    $("console").scrollTop = $("console").scrollHeight;
    $("processDetails").innerHTML = `<b>Status</b><span>${esc(procText)}</span><b>PID</b><span>${esc(process.pid || "—")}</span><b>Runs dir</b><span>${esc(process.runs_dir || "—")}</span><b>Command</b><span>${esc((process.command || []).join(" "))}</span>`;
    renderRuns(); renderMissionTrace(tracePayload); renderAgentLiveView(liveViewPayload); renderAdaptiveMetric(worldModelPayload?.summary||{}); updateButtons();
  } catch (error) {
    $("backendBadge").textContent = "Backend offline"; $("backendBadge").className = "badge bad";
    $("processMetric").textContent = "Offline";
  }
}

function updateButtons() {
  const running = !!state.process?.running;
  const paused = !!state.process?.paused;
  const ready = !!state.preflight?.pass && !!state.preflight?.autogen?.pass && !!state.liveReadiness?.pass && !!state.liveReadiness?.token;
  $("startBtn").disabled = running || !ready;
  $("pauseBtn").disabled = !running || paused;
  $("resumeBtn").disabled = !running || !paused;
  $("stopBtn").disabled = !running;
}

function renderRuns() {
  $("runsList").innerHTML = state.runs.length ? state.runs.map(r=>`<div class="run"><strong>${esc(r.run_id)}</strong><span>${esc(r.path)}</span></div>`).join("") : '<div class="empty">No runs discovered.</div>';
}

async function loadCapabilities() {
  try {
    const qs = new URLSearchParams({ config: $("configPath").value || "config.yaml", page_family: $("capFamily").value, text: $("capSearch").value });
    const data = await api(`/api/capabilities?${qs}`);
    const m=data.manifest||{}; $("capabilitySummary").textContent=`${data.count||0} shown • ${m.capability_count||0} total • ${m.bootstrap_source_count||0} canonical seed source(s) • live successes are marked validated_live`;
    $("capabilitiesTable").innerHTML = table(data.capabilities || [], [
      {key:"capability_id",label:"ID"},{key:"page_family",label:"Family"},{key:"label",label:"Capability"},{key:"action",label:"Action"},{key:"knowledge_source",label:"Source"},{key:"trust",label:"Trust"},{key:"verified",label:"Live verified"},
    ]);
  } catch(e){ toast(e.message,"bad"); }
}

async function loadApis() {
  try {
    const qs = new URLSearchParams({ config: $("configPath").value || "config.yaml", page_family: $("apiFamily").value, method: $("apiMethod").value });
    const data = await api(`/api/apis?${qs}`);
    const m=data.manifest||{}; $("apiSummary").textContent=`${data.count||0} shown • ${m.api_contract_count||0} total • canonical contracts are available immediately and live network observations update trust`;
    $("apisTable").innerHTML = table(data.api_contracts || [], [
      {key:"method",label:"Method"},{key:"endpoint",label:"Endpoint",render:r=>esc(r.endpoint || r.endpoint_template || r.url || "")},{key:"response_statuses",label:"Statuses",render:r=>esc((r.response_statuses||[]).join(", ") || "—")},{key:"page_families",label:"Families",render:r=>esc((r.page_families||[]).join(", "))},{key:"knowledge_source",label:"Source"},{key:"trust",label:"Trust"},
    ]);
  } catch(e){ toast(e.message,"bad"); }
}

function taskRequest(task) { return {
  task, config:$("configPath").value||"config.yaml", runs_dir:$("runsDir").value||"./runs",
  input_json:$("taskInputJson").value.trim()||"./input.json", input_root:$("taskInputRoot").value.trim(),
  start_url:$("taskStartUrl").value.trim(), deep_learn:$("taskDeepLearn").checked,
  allow_portal_mutation:$("taskAllowMutation").checked, confirmation:$("taskMutationConfirmation").value.trim(),
  operator_role:$("taskOperatorRole")?.value.trim()||"", approval_id:$("taskApprovalId")?.value.trim()||"",
  force_repeat_mutation:!!$("taskForceRepeatMutation")?.checked, allow_adaptive_exploration:true
}; }
async function taskAction(kind) {
  try {
    const task=$("taskText").value.trim(); if(!task) throw new Error("Enter a task first.");
    const data=await api(`/api/portal-task/${kind}`,{method:"POST",body:JSON.stringify(taskRequest(task))});
    $("taskOutput").textContent=JSON.stringify(data,null,2); if(kind==="run") showTab("mission");
  } catch(e){$("taskOutput").textContent=e.message;toast(e.message,"bad");}
}


function productionTaskRequest(task) {
  const base=taskRequest(task);
  return {
    task, config:base.config, input_json:base.input_json, input_root:base.input_root,
    start_url:base.start_url, runs_dir:base.runs_dir, golden_dir:$("goldenDir").value.trim(),
    uploads_dir:$("uploadsDir").value.trim(), deep_learn:base.deep_learn,
    allow_portal_mutation:base.allow_portal_mutation, confirmation:base.confirmation,
    operator_role:base.operator_role, approval_id:base.approval_id,
    force_repeat_mutation:base.force_repeat_mutation, mutation_expected:base.allow_portal_mutation
  };
}
async function productionAction(kind) {
  try {
    const task=$("taskText").value.trim(); if(!task) throw new Error("Enter a task first.");
    const payload=productionTaskRequest(task);
    const data=await api(`/api/production/${kind}`,{method:"POST",body:JSON.stringify(payload)});
    $("taskOutput").textContent=JSON.stringify(data,null,2);
    if(kind==="start"){toast(`Production E2E started (PID ${data.pid||"—"})`);showTab("mission");}
    else toast(data.pass?"Production doctor: GO":"Production doctor: NO-GO",data.pass?"good":"bad");
  } catch(e){$("taskOutput").textContent=e.message;toast(e.message,"bad");}
}

async function loadSkillLibrary() {
  try {
    const qs=new URLSearchParams({
      config:$('configPath').value||'config.yaml',
      query:$('skillLibrarySearch')?.value||'',
      status:$('skillLibraryStatus')?.value||'',
      limit:'100'
    });
    const data=await api(`/api/skills?${qs}`), m=data.manifest||{};
    if ($('skillLibrarySummary')) $('skillLibrarySummary').textContent=`${m.skill_count||0} skills • ${m.validated_skill_count||0} validated • ${m.drift_suspect_count||0} drift suspect • ${m.stale_skill_count||0} stale`;
    if ($('skillLibraryTable')) $('skillLibraryTable').innerHTML=table(data.skills||[],[
      {key:'name',label:'Skill'},
      {key:'status',label:'Status'},
      {key:'effective_confidence',label:'Effective confidence',render:r=>scoreText(r.effective_confidence)},
      {key:'success_count',label:'Successes'},
      {key:'failure_count',label:'Failures'},
      {key:'age_days',label:'Age (days)',render:r=>esc(Number(r.age_days||0).toFixed(1))},
      {key:'last_success_at',label:'Last verified'}
    ]);
  } catch(e){ if($('skillLibraryTable')) $('skillLibraryTable').innerHTML=`<div class="empty">${esc(e.message)}</div>`; }
}

async function loadReplayPolicy() {
  try {
    const qs=new URLSearchParams({config:$('configPath').value||'config.yaml',limit:'100'});
    const data=await api(`/api/replay-policy?${qs}`), m=data.manifest||{};
    if ($('replayPolicySummary')) $('replayPolicySummary').textContent=`${m.episode_count||0} replay episodes • ${m.policy_count||0} learned policies • ${m.exploitation_policy_count||0} exploitation • ${m.hybrid_policy_count||0} hybrid • ${m.exploration_policy_count||0} exploration`;
    if ($('replayPolicyTable')) $('replayPolicyTable').innerHTML=table(data.policies||[],[
      {key:'mode',label:'Mode'},
      {key:'winner',label:'Dream winner'},
      {key:'confidence',label:'Confidence',render:r=>scoreText(r.confidence)},
      {key:'support',label:'Runs'},
      {key:'success_rate',label:'Success rate',render:r=>scoreText(r.success_rate)},
      {key:'average_score',label:'Avg score',render:r=>scoreText(r.average_score)},
      {key:'workflow_step_count',label:'Fast steps'},
      {key:'policy_version',label:'Policy v'}
    ]);
    if ($('replayRunHistory')) $('replayRunHistory').textContent=JSON.stringify({old_run_import:data.old_run_import,recent_runs:m.recent_runs||[],cache:m.policy_cache_path,dreams:m.dream_cycles_path},null,2);
  } catch(e){ if($('replayPolicyTable')) $('replayPolicyTable').innerHTML=`<div class="empty">${esc(e.message)}</div>`; }
}



window.pickTeachControl = function(id,label,section,role){
  const control={semantic_control_id:decodeURIComponent(id||''),label:decodeURIComponent(label||''),section:decodeURIComponent(section||''),role:decodeURIComponent(role||'')};
  state.humanSelectedControl=control;
  if($('humanSemanticControl')) $('humanSemanticControl').value=control.semantic_control_id || control.label || '';
  if($('humanControlLabel')) $('humanControlLabel').value=control.label||'';
  if($('humanControlSection')) $('humanControlSection').value=control.section||'';
  if($('humanControlRole')) $('humanControlRole').value=control.role||'';
  toast(`Selected live control: ${control.label||control.semantic_control_id||'control'}`, 'info');
};

async function loadInteractiveTeaching(){
  try{
    const qs=new URLSearchParams({config:$('configPath').value||'config.yaml'});
    const data=await api(`/api/interactive-teaching?${qs}`); state.interactiveTeaching=data;
    const active=(data.active||[])[0]||null; state.interactiveTeachingSession=active;
    const badge=$('interactiveTeachState');
    if(!badge) return;
    if(!active){badge.textContent='Interactive teaching idle';badge.className='badge neutral';return;}
    badge.textContent=active.status==='recording'?`Recording ${active.phase||'HIP'} demonstration`:`Teaching captured; waiting for agent reproof`;
    badge.className='badge info';
  }catch(e){ if($('interactiveTeachState')){$('interactiveTeachState').textContent='Teaching unavailable';$('interactiveTeachState').className='badge bad';} }
}

async function startInteractiveTeaching(){
  try{
    const review=state.humanPhaseReviewRequest||{};
    const missionRun=(state.missionTrace&&state.missionTrace.run_id)||review.run_id||'';
    const phase=review.phase||'';
    if(!missionRun) throw new Error('Start a mission before interactive teaching.');
    const data=await api('/api/interactive-teaching/start',{method:'POST',body:JSON.stringify({
      run_id:missionRun,phase,task:`Teach ${review.phase_display||phase||'current HIP flow'}`,note:$('humanTeachNote')?.value||'',config:$('configPath').value||'config.yaml'
    })});
    state.interactiveTeachingSession=data.session||null;
    toast('Interactive teaching started. Use the real HIP browser and navigate/click the correct path; then return here and press Finish & learn.','info');
    await loadInteractiveTeaching();
  }catch(e){toast(e.message,'bad');}
}

async function finishInteractiveTeaching(){
  try{
    const session=state.interactiveTeachingSession||{};
    if(!session.session_id) throw new Error('No interactive teaching session is recording.');
    const data=await api('/api/interactive-teaching/finish',{method:'POST',body:JSON.stringify({
      session_id:session.session_id,note:$('humanTeachNote')?.value||'',config:$('configPath').value||'config.yaml'
    })});
    state.interactiveTeachingSession=data.session||session;
    toast('Demonstration finished. Now press Looks correct after the page is correct; the agent will capture, exact-reprove and promote this path.','good');
    await loadInteractiveTeaching();
  }catch(e){toast(e.message,'bad');}
}

async function loadHumanAssistance(){
  try{
    const qs=new URLSearchParams({config:$('configPath').value||'config.yaml'});
    const data=await api(`/api/human-assistance?${qs}`); state.humanAssistance=data;
    const req=(data.pending||[])[0]||null; const badge=$('humanAssistState'), reason=$('humanAssistReason'), select=$('humanInputPath');
    if(!req){
      if(badge){badge.textContent='No request';badge.className='badge neutral';}
      if(reason) reason.textContent='No field currently needs human teaching.';
      if(select) select.innerHTML='<option value="">No unresolved field</option>';
      return;
    }
    if(badge){badge.textContent='Needs assistance';badge.className='badge info';}
    if(reason) reason.textContent=req.reason||req.instruction||'Choose the correct live control.';
    if(select){select.innerHTML=(req.unresolved_input_paths||[]).map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join(''); select.dataset.requestId=req.request_id||'';}
  }catch(e){ if($('humanAssistState')){$('humanAssistState').textContent='Unavailable';$('humanAssistState').className='badge bad';} }
}

async function loadHumanPhaseReview(){
  try{
    const qs=new URLSearchParams({config:$('configPath').value||'config.yaml'});
    const data=await api(`/api/human-phase-review?${qs}`); state.humanPhaseReview=data;
    const req=(data.pending||[])[0]||null; const badge=$('humanPhaseReviewState'), reason=$('humanPhaseReviewReason'), meta=$('humanPhaseReviewMeta');
    if(!req){
      state.humanPhaseReviewRequest=null;
      if(badge){badge.textContent='No review';badge.className='badge neutral';}
      if(reason) reason.textContent='No newly learned phase is waiting for human confirmation.';
      if(meta) meta.textContent='';
      return;
    }
    state.humanPhaseReviewRequest=req;
    const verdict=(req.automated_verdict||'').toUpperCase();
    if(badge){badge.textContent=`Review ${req.phase_display||req.phase||''}`;badge.className='badge info';}
    if(reason) reason.textContent=req.reason||req.instruction||'Review the newly learned phase once.';
    if(meta) meta.textContent=`Automated: ${verdict||'UNKNOWN'} • deterministic: ${req.deterministic_pass?'PASS':'not proven'} • exact checkpoint: ${req.exact_checkpoint_pass?'PASS':'not proven'}${req.model_consensus?.used?` • model panel ${req.model_consensus.pass_votes||0}/${req.model_consensus.vote_count||0} pass`:''}`;
  }catch(e){ if($('humanPhaseReviewState')){$('humanPhaseReviewState').textContent='Unavailable';$('humanPhaseReviewState').className='badge bad';} }
}

async function submitHumanPhaseReview(verdict){
  try{
    const req=state.humanPhaseReviewRequest||{};
    if(!req.request_id) throw new Error('No phase review is currently pending.');
    const data=await api('/api/human-phase-review/resolve',{method:'POST',body:JSON.stringify({
      request_id:req.request_id,verdict,note:$('humanPhaseReviewNote')?.value||'',reviewer:'control-center-user',config:$('configPath').value||'config.yaml'
    })});
    toast(verdict==='pass'?'Phase confirmed. The agent can continue and learn from this approval.':'Correction recorded. The self-heal/policy loop will treat this as supervised failure evidence.',verdict==='pass'?'good':'info');
    if($('humanPhaseReviewNote')) $('humanPhaseReviewNote').value='';
    await loadHumanPhaseReview();
    await loadInteractiveTeaching();
    return data;
  }catch(e){toast(e.message,'bad');}
}

async function submitHumanTeaching(){
  try{
    const select=$('humanInputPath'); const requestId=select?.dataset?.requestId||''; const inputPath=select?.value||'';
    const control=state.humanSelectedControl||{};
    if(!requestId||!inputPath) throw new Error('No unresolved assistance request is selected.');
    if(!(control.semantic_control_id||control.label)) throw new Error('Click the correct candidate/control in Agent Live View first.');
    const data=await api('/api/human-assistance/teach',{method:'POST',body:JSON.stringify({
      request_id:requestId,input_path:inputPath,semantic_control_id:control.semantic_control_id||'',control_label:control.label||'',section:control.section||'',role:control.role||'',note:$('humanTeachNote')?.value||'',verified_by_human:true,config:$('configPath').value||'config.yaml'
    })});
    toast('Teaching recorded. Retry/resume the task; this mapping will be live re-proved before use.','good');
    state.humanSelectedControl=null; if($('humanSemanticControl')) $('humanSemanticControl').value=''; await loadHumanAssistance();
    return data;
  }catch(e){toast(e.message,'bad');}
}

async function loadModelPortfolio() {
  try {
    const qs=new URLSearchParams({config:$('configPath').value||'config.yaml'});
    const data=await api(`/api/model-portfolio?${qs}&probe=true`), m=data.manifest||{}, rows=[];
    const rankings=m.rankings||{};
    Object.keys(rankings).sort().forEach(role=>{
      (rankings[role]||[]).forEach((r,i)=>rows.push({role,rank:i+1,model:r.model,score:r.score,trials:r.trials,champion:(m.role_champions||{})[role]===r.model?'yes':''}));
    });
    if ($('modelPortfolioSummary')) $('modelPortfolioSummary').textContent=`${(m.text_models||[]).length} configured text • ${(m.available_text_models||[]).length} available • ${(m.recent_distinct_models||[]).length} recently used • ${(m.vision_models||[]).length} vision • cycle ${m.cycle||0} • learning=${m.learning_forces_portfolio?'multi-model':'adaptive'}`;
    if ($('modelPortfolioTable')) $('modelPortfolioTable').innerHTML=table(rows,[
      {key:'role',label:'Role'},{key:'rank',label:'#'},{key:'model',label:'Model'},{key:'score',label:'Score',render:r=>scoreText(r.score)},{key:'trials',label:'Trials'},{key:'champion',label:'Champion'}
    ]);
  } catch(e){ if($('modelPortfolioTable')) $('modelPortfolioTable').innerHTML=`<div class="empty">${esc(e.message)}</div>`; }
}

async function loadRecursiveImprovement() {
  try {
    const qs=new URLSearchParams({config:$('configPath').value||'config.yaml'});
    const data=await api(`/api/recursive-improvement?${qs}`);
    if ($('recursiveImprovementState')) $('recursiveImprovementState').textContent=JSON.stringify(data,null,2);
  } catch(e){ if($('recursiveImprovementState')) $('recursiveImprovementState').textContent=e.message; }
}

function changeRequest() { return { task:$("changeText").value.trim(), config:$("configPath").value||"config.yaml", runs_dir:$("runsDir").value||"./runs", allow_adaptive_exploration:true, operator_role:$("operatorRole").value.trim(), approval_id:$("approvalId").value.trim(), allow_portal_mutation:$("allowPortalMutation").checked, confirmation:$("mutationConfirmation").value }; }
async function changeAction(kind) {
  try { const payload=changeRequest(); if(!payload.task) throw new Error("Describe the change first."); const data=await api(`/api/governed-change/${kind}`,{method:"POST",body:JSON.stringify(payload)}); $("governanceOutput").textContent=JSON.stringify(data,null,2); if(kind==="run") showTab("mission"); }
  catch(e){$("governanceOutput").textContent=e.message;toast(e.message,"bad");}
}
async function loadAudit(){try{$("governanceOutput").textContent=JSON.stringify(await api(`/api/governed-change/audit?config=${encodeURIComponent($("configPath").value||"config.yaml")}&limit=50`),null,2)}catch(e){toast(e.message,"bad")}}

function showTab(name) { document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("active",b.dataset.tab===name)); document.querySelectorAll(".tab-panel").forEach(p=>p.classList.toggle("active",p.id===name)); }

function wire() {
  $("sectionSelect").addEventListener("change",()=>{ if(!$("useDescriptionTrigger").checked){state.descriptionPlan=null;} invalidateLiveReadiness("Execution scope changed"); renderScope();runPreflight(true)});
  $("descriptionPlanBtn").onclick=()=>planDescription().catch(err=>toast(err.message,"bad"));
  $("missionDescription").addEventListener("input",()=>{state.descriptionPlan=null; invalidateLiveReadiness("Mission description changed"); if($("useDescriptionTrigger").checked) renderScope();});
  $("useDescriptionTrigger").addEventListener("change",()=>{invalidateLiveReadiness("Description trigger changed");renderScope();runPreflight(true)});
  $("apiMode").addEventListener("change",()=>{
    if ($("liveWitnessMode")?.checked && $("apiMode").value === "write") { $("apiMode").value="capture"; toast("Live witness forces API mode to capture", "info"); }
    invalidateLiveReadiness("API mode changed");$("apiMetric").textContent=$("apiMode").value;
  });
  $("liveWitnessMode").addEventListener("change",()=>{syncWitnessMode();invalidateLiveReadiness("Execution profile changed")});
  $("allowApiMutation").addEventListener("change",()=>{if($("liveWitnessMode")?.checked) $("allowApiMutation").checked=false; invalidateLiveReadiness("Mutation authorization changed")});
  ["configPath","runsDir","goldenDir","uploadsDir","inputPath","resumeRun"].forEach(id=>$(id)?.addEventListener("input",()=>invalidateLiveReadiness(`${id} changed`)));
  $("inputFile").addEventListener("change",e=>e.target.files?.[0]&&uploadInput(e.target.files[0]).catch(err=>toast(err.message,"bad")));
  $("testTextModelBtn").onclick=()=>testModelAvailability("text"); $("testVisionModelBtn").onclick=()=>testModelAvailability("vision");
  $("preflightBtn").onclick=()=>runPreflight(); $("liveRuntimeCertBtn").onclick=()=>runLiveRuntimeCertification(); $("liveReadinessBtn").onclick=()=>runLiveReadiness(); $("startBtn").onclick=startMission; $("pauseBtn").onclick=()=>processAction("pause"); $("resumeBtn").onclick=()=>processAction("resume"); $("stopBtn").onclick=()=>processAction("stop");
  $("refreshBtn").onclick=refreshStatus; $("runsRefresh").onclick=refreshStatus; $("capLoad").onclick=loadCapabilities; $("apiLoad").onclick=loadApis; if($("humanAssistRefresh")) $("humanAssistRefresh").onclick=loadHumanAssistance; if($("humanTeachSubmit")) $("humanTeachSubmit").onclick=submitHumanTeaching; if($("interactiveTeachStart")) $("interactiveTeachStart").onclick=startInteractiveTeaching; if($("interactiveTeachFinish")) $("interactiveTeachFinish").onclick=finishInteractiveTeaching; if($("humanPhaseRefresh")) $("humanPhaseRefresh").onclick=loadHumanPhaseReview; if($("humanPhaseApprove")) $("humanPhaseApprove").onclick=()=>submitHumanPhaseReview('pass'); if($("humanPhaseReject")) $("humanPhaseReject").onclick=()=>submitHumanPhaseReview('needs_correction');
  $("taskPlan").onclick=()=>taskAction("plan"); $("taskRun").onclick=()=>taskAction("run"); if($("productionDoctor")) $("productionDoctor").onclick=()=>productionAction("doctor"); if($("productionRun")) $("productionRun").onclick=()=>productionAction("start"); if($("skillLibraryLoad")) $("skillLibraryLoad").onclick=loadSkillLibrary; if($("skillLibrarySearch")) $("skillLibrarySearch").addEventListener("input",()=>loadSkillLibrary()); if($("skillLibraryStatus")) $("skillLibraryStatus").addEventListener("change",()=>loadSkillLibrary()); if($("replayPolicyLoad")) $("replayPolicyLoad").onclick=loadReplayPolicy; if($("modelPortfolioLoad")) $("modelPortfolioLoad").onclick=loadModelPortfolio; if($("recursiveImprovementLoad")) $("recursiveImprovementLoad").onclick=loadRecursiveImprovement; $("changePreview").onclick=()=>changeAction("preview"); $("changeRun").onclick=()=>changeAction("run"); $("auditLoad").onclick=loadAudit;
  document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>showTab(b.dataset.tab));
}

async function boot() {
  wire();
  syncWitnessMode();
  try { await loadSections(); } catch(e){ toast(`Could not load sections: ${e.message}`,"bad"); }
  await Promise.all([refreshStatus(), runPreflight(true)]);
  loadCapabilities(); loadApis(); loadSkillLibrary(); loadReplayPolicy(); loadModelPortfolio(); loadRecursiveImprovement(); loadHumanAssistance(); loadHumanPhaseReview(); loadInteractiveTeaching();
  clearInterval(state.timer); state.timer=setInterval(()=>{refreshStatus();loadHumanAssistance();loadHumanPhaseReview();loadInteractiveTeaching();},3000);
}
boot();
