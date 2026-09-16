from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

class ApiCandidate(BaseModel):
    url: str
    method: str
    score: int
    confidence: Literal["LOW","MEDIUM","HIGH"]
    request_body_shape: dict[str,Any] = Field(default_factory=dict)
    query_params: dict[str,Any] = Field(default_factory=dict)
    safe_request_values: dict[str,Any] = Field(default_factory=dict)
    request_content_type: str|None = None
    response_shape: dict[str,Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    sample_count: int = 1
    sample_job_hint: dict[str,Any] = Field(default_factory=dict)
    observed_list_length: int|None = None
    observed_total: int|None = None
    observed_has_more: bool|None = None
    observed_unique_ids: int|None = None
    detail_url_field: str|None = None
    detail_url_coverage: float = 0.0
    homogeneity_score: float = 0.0
    job_entity_density: float = 0.0
    rejection_reasons: list[str] = Field(default_factory=list)
    observed_company_count: int = 0
    list_item_path: str|None = None
    replayable: bool = True
    graphql_operation: str|None = None
    graphql_page_info_path: str|None = None
    source_type: Literal["NETWORK_JSON","GRAPHQL","SERIALIZED_STATE"] = "NETWORK_JSON"
    source_index: int|None = None
    provenance: ProvenanceRecord|None = None
    observed_phase: str|None = None

class CandidateSource(BaseModel):
    source_type: Literal["NETWORK_JSON","GRAPHQL","SERIALIZED_STATE","RUNTIME_STATE","DOM_LIST","IFRAME","EMBEDDED_WIDGET"]
    url: str|None = None
    status: Literal["CANDIDATE","REJECTED","OBSERVED","NONE"] = "OBSERVED"
    score: int = 0
    confidence: Literal["LOW","MEDIUM","HIGH"] = "LOW"
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str,Any] = Field(default_factory=dict)

class ATSFingerprint(BaseModel):
    host: str
    api_route_patterns: list[str] = Field(default_factory=list)
    http_methods: list[str] = Field(default_factory=list)
    list_response_shape: dict[str,Any] = Field(default_factory=dict)
    detail_response_shape: dict[str,Any] = Field(default_factory=dict)
    job_id_fields: list[str] = Field(default_factory=list)
    title_fields: list[str] = Field(default_factory=list)
    pagination_model: str = "UNKNOWN"
    scope_model: dict[str,Any] = Field(default_factory=dict)
    detail_url_pattern: str|None = None
    public_identifiers: dict[str,Any] = Field(default_factory=dict)

class ATSProfile(BaseModel):
    profile_id: str
    version: str = "1"
    confidence: Literal["LOW","MEDIUM","HIGH"] = "LOW"
    fingerprint: ATSFingerprint
    list_endpoint: str|None = None
    list_method: str|None = None
    list_path: str|None = None
    pagination: dict[str,Any] = Field(default_factory=dict)
    detail: dict[str,Any] = Field(default_factory=dict)
    scope: dict[str,Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)

ProviderCapability = Literal["PLAIN_HTTP_API","BROWSER_API","ENCRYPTED_BROWSER_API","DOM_ONLY","KNOWN_ADAPTER","UNSUPPORTED"]

class RuntimeJobSource(BaseModel):
    mode: Literal["BROWSER_RUNTIME_DATA"] = "BROWSER_RUNTIME_DATA"
    provider: str = "UNKNOWN"
    capability: str = "ENCRYPTED_BROWSER_API"
    mechanism: Literal["STATE","TRANSFORM","DOM","HOOK","OTHER"] = "OTHER"
    source_path: str|None = None
    record_count: int = 0
    unique_ids: int = 0
    job_id_field: str|None = None
    job_title_field: str|None = None
    location_field: str|None = None
    department_field: str|None = None
    jd_fields: list[str] = Field(default_factory=list)
    pagination: dict[str,Any] = Field(default_factory=dict)
    pagination_model: Literal["OFFSET","UNKNOWN"] = "UNKNOWN"
    offset_field: str|None = None
    limit_field: str|None = None
    initial_offset: int|None = None
    limit: int|None = None
    total: int|None = None
    page_count: int|None = None
    trigger_mode: Literal["OFFSET_PAGE","OFFSET_BUTTON","OFFSET_SCROLL","OTHER","UNKNOWN"] = "UNKNOWN"
    pagination_validated: bool = False
    records: list[dict[str,Any]] = Field(default_factory=list)
    # STEP 54B: generic detail-request contract observed from runtime
    # evidence (an organically triggered detail request + its response
    # shape + runtime state values). Empty when no trustworthy detail
    # request was observed; consumers must then fail safe (no fabricated JD).
    detail_contract: dict[str,Any] = Field(default_factory=dict)
    confidence: Literal["LOW","MEDIUM","HIGH"] = "LOW"
    executable: bool = False
    evidence: list[str] = Field(default_factory=list)
    validator_errors: list[str] = Field(default_factory=list)

class ProviderSignal(BaseModel):
    signal_type: str
    value: str
    weight: int = 0
    independent_group: str = "other"

class ProviderFingerprint(BaseModel):
    provider: str = "UNKNOWN"
    confidence: Literal["LOW","MEDIUM","HIGH"] = "LOW"
    evidence: list[str] = Field(default_factory=list)
    capabilities: list[ProviderCapability] = Field(default_factory=list)
    signals: list[ProviderSignal] = Field(default_factory=list)
    encrypted_envelope: bool = False
    hostname_matched: bool = False
    independent_signal_groups: int = 0

class RecruitmentEntry(BaseModel):
    entry_type: Literal["CAMPUS","SOCIAL","INTERN","ALL_JOBS","OVERSEAS","OTHER"]
    text: str
    url: str
    destination_host: str
    evidence: list[str] = Field(default_factory=list)
    adapter_platform: str|None = None
    adapter_name: str|None = None
    trust_status: Literal["TRUSTED","REJECTED","UNKNOWN"] = "UNKNOWN"
    destination_classification: Literal["KNOWN_ATS","UNKNOWN_ATS","DIRECT_JOB_SITE","RECRUITMENT_PORTAL","NON_JOB_DESTINATION"] = "RECRUITMENT_PORTAL"
    ats_fingerprint: ATSFingerprint|None = None
    action_id: str|None = None
    action_type: str|None = None

class RecruitmentAction(BaseModel):
    control_id: str
    text: str
    target: str|None = None
    action_type: str = "OTHER"
    scope_semantics: str = "UNKNOWN"
    job_semantics: list[str] = Field(default_factory=list)
    navigation_confidence: str = "LOW"
    source_element: str|None = None
    href: str|None = None
    onclick: str|None = None
    role: str|None = None
    data_route: str|None = None
    data_url: str|None = None
    score: int = 0

class ActionAttempt(BaseModel):
    action: RecruitmentAction
    before_url: str
    after_url: str
    result: str
    dom_changed: bool = False
    new_job_candidates: int = 0
    new_recruitment_entries: int = 0
    error: str|None = None

class ProvenanceRecord(BaseModel):
    origin_url: str
    url: str
    host: str
    node_id: str|None = None
    parent_node_id: str|None = None
    parent_url: str|None = None
    trigger_action_id: str|None = None
    trigger_action_text: str|None = None
    trigger_action_type: str|None = None
    redirect_chain: list[str] = Field(default_factory=list)
    scope_at_origin: dict[str,Any] = Field(default_factory=dict)
    scope_at_node: dict[str,Any] = Field(default_factory=dict)
    company_context_evidence: list[str] = Field(default_factory=list)
    depth: int = 0
    trust: str = "UNKNOWN"
    rejected_reason: str|None = None

class TerminalCandidate(BaseModel):
    candidate: str
    provenance_path: list[ProvenanceRecord] = Field(default_factory=list)
    scope: str = "UNKNOWN"
    company_continuity: str = "UNKNOWN"
    trust: str = "UNKNOWN"
    plan: str = "UNKNOWN"
    selected: bool = False
    rejected_reason: str|None = None
    provenance_score: int = 0
    scope_score: int = 0
    company_score: int = 0
    adapter_score: int = 0
    job_evidence_score: int = 0
    depth: int = 0

class TerminalActivationTrace(BaseModel):
    terminal_url: str
    resolved_url: str|None = None
    scope: str = "UNKNOWN"
    activation_started_at: datetime
    activation_finished_at: datetime|None = None
    elapsed: float = 0.0
    terminal_status: str|None = None
    upstream_entry_action: RecruitmentAction|None = None
    terminal_actions_attempted: list[dict[str,Any]] = Field(default_factory=list)
    network: list[dict[str,Any]] = Field(default_factory=list)
    dom_evidence: list[dict[str,Any]] = Field(default_factory=list)
    serialized_state: list[dict[str,Any]] = Field(default_factory=list)
    frames: list[dict[str,Any]] = Field(default_factory=list)
    candidate_lifecycle: list[dict[str,Any]] = Field(default_factory=list)
    empty_terminal_diagnostic: dict[str,Any] = Field(default_factory=dict)

class TerminalActivationRecord(BaseModel):
    status: str
    terminal_url: str
    resolved_url: str
    scope: str
    company_continuity: str
    provenance_path: list[ProvenanceRecord] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    budget_seconds: float = 0.0
    observed_requests: int = 0
    attributed_candidates: int = 0
    terminal_actions_attempted: int = 0
    selected_source: str|None = None
    adapter_name: str|None = None
    plan_confidence: str|None = None
    plan_valid: bool = False
    failure_reason: str|None = None
    provider: str|None = None
    capability: str|None = None
    fingerprint_confidence: str|None = None
    routing_decision: str|None = None
    trace: TerminalActivationTrace|None = None

class HandoffProvenance(BaseModel):
    source_url: str
    portal_type: str
    entry_type: str
    entry_text: str
    entry_url: str
    destination_host: str
    detected_platform: str|None = None
    adapter_selected: str|None = None
    scope_inherited: dict[str,Any] = Field(default_factory=dict)

class NavigationNode(BaseModel):
    url: str
    depth: int
    page_type: str
    scope: dict[str,Any] = Field(default_factory=dict)
    recruitment_semantics: list[str] = Field(default_factory=list)
    known_adapter: str|None = None
    ats_classification: str|None = None
    job_list_evidence: bool = False
    visited: bool = True
    inherited_intent: str = "UNKNOWN"
    local_intent: str = "UNKNOWN"
    effective_intent: str = "UNKNOWN"

class NavigationEdge(BaseModel):
    source: str
    target: str
    entry_type: str
    text: str
    score: int = 0
    inherited_intent: str = "UNKNOWN"
    local_intent: str = "UNKNOWN"
    effective_intent: str = "UNKNOWN"
    compatibility: int = 0
    trust: str = "UNKNOWN"
    trust_evidence: list[str] = Field(default_factory=list)

class NavigationGraph(BaseModel):
    nodes: list[NavigationNode] = Field(default_factory=list)
    edges: list[NavigationEdge] = Field(default_factory=list)
    max_depth: int = 3
    terminal_reason: str|None = None
    source_intent: str = "UNKNOWN"
    selected_scope: str|None = None
    path_explanation: str|None = None
    rejected_alternatives: list[str] = Field(default_factory=list)
    available_scopes: list[str] = Field(default_factory=list)
    stability_state: Literal["STABLE","MOSTLY_STABLE","UNSTABLE"] = "STABLE"
    stability_attempts: int = 1
    stability_reason: str|None = None
    terminal_candidates: list[TerminalCandidate] = Field(default_factory=list)

class DiscoveryStability(BaseModel):
    state: Literal["STABLE","MOSTLY_STABLE","UNSTABLE"]
    attempts: int = 1
    signatures: list[str] = Field(default_factory=list)
    reason: str|None = None

class VisibleTotalEvidence(BaseModel):
    value: int
    source_text: str
    confidence: Literal["MEDIUM","HIGH"]
    evidence_type: str
    locator: str|None = None
    container_id: str|None = None
    proximity_score: int = 0
    semantics: Literal["GLOBAL_TOTAL","SCOPED_TOTAL","CURRENT_PAGE_COUNT","TOTAL_RESULTS","RANGE_END","PAGE_NUMBER","UNKNOWN_TOTAL"] = "UNKNOWN_TOTAL"

class JobCard(BaseModel):
    title: str|None = None
    location: str|None = None
    department: str|None = None
    metadata: str|None = None
    detail_link: str|None = None
    apply_link: str|None = None
    structure_signature: str
    explicit_id: str|None = None

class ListContainer(BaseModel):
    container_id: str
    locator: str|None = None
    job_card_count: int = 0
    job_card_structure_signature: str|None = None
    unique_titles: int = 0
    unique_detail_links: int = 0
    scope_context: dict[str,Any] = Field(default_factory=dict)
    nearby_heading: str|None = None
    nearby_visible_totals: list[VisibleTotalEvidence] = Field(default_factory=list)
    pagination_controls: list[str] = Field(default_factory=list)
    bound_total: int|None = None
    total_conflict: bool = False

class RejectedCandidate(BaseModel):
    url: str
    method: str
    score: int
    reasons: list[str] = Field(default_factory=list)
    response_shape: dict[str,Any] = Field(default_factory=dict)

class PaginationDetection(BaseModel):
    pagination_type: Literal["PAGE","OFFSET","CURSOR","GRAPHQL_PAGE","GRAPHQL_OFFSET","GRAPHQL_CURSOR","LOAD_MORE","INFINITE_SCROLL","UNKNOWN"] = "UNKNOWN"
    page_param: str|None = None
    page_size_param: str|None = None
    cursor_param: str|None = None
    total_field: str|None = None
    next_cursor_field: str|None = None
    has_more_field: str|None = None
    observed_change: str|None = None
    inference_source: str|None = None
    confidence: float|None = None
    evidence: list[str] = Field(default_factory=list)
    first_page: int|None = None
    page_size: int|None = None

class NetworkSummary(BaseModel):
    observed_requests: int = 0
    xhr_fetch_requests: int = 0
    json_candidates: int = 0
    job_api_candidates: int = 0
    phase_json_candidates: dict[str,int] = Field(default_factory=dict)
    observation_policy: dict[str,Any] = Field(default_factory=dict)
    boot_recovery: dict[str,Any] = Field(default_factory=dict)

class DiscoveryResult(BaseModel):
    source_url: str
    status: Literal["DISCOVERED","PARTIAL","NOT_FOUND","BLOCKED"]
    candidate_list_apis: list[ApiCandidate] = Field(default_factory=list)
    candidate_detail_apis: list[ApiCandidate] = Field(default_factory=list)
    probable_list_api: ApiCandidate|None = None
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    detected_pagination: PaginationDetection = Field(default_factory=PaginationDetection)
    detected_scope: dict[str,Any] = Field(default_factory=dict)
    network_summary: NetworkSummary = Field(default_factory=NetworkSummary)
    dom_fallback: dict[str,Any] = Field(default_factory=dict)
    detail_dom: dict[str,Any] = Field(default_factory=dict)
    company: str|None = None
    tool_version: str = ""
    warnings: list[str] = Field(default_factory=list)
    source_inventory: list[CandidateSource] = Field(default_factory=list)
    page_type: Literal["JOB_LIST","JOB_DETAIL","RECRUITMENT_PORTAL","CAMPAIGN_PAGE","ATS_EMBED","UNKNOWN"] = "UNKNOWN"
    recruitment_entries: list[RecruitmentEntry] = Field(default_factory=list)
    internal_navigation_trace: list[dict[str,Any]] = Field(default_factory=list)
    handoff: HandoffProvenance|None = None
    ats_classification: Literal["KNOWN_ATS","UNKNOWN_ATS","DIRECT_JOB_SITE","RECRUITMENT_PORTAL","NON_JOB_DESTINATION"] = "NON_JOB_DESTINATION"
    ats_profile: ATSProfile|None = None
    provider_fingerprint: ProviderFingerprint|None = None
    runtime_source: RuntimeJobSource|None = None
    discovery_stability: DiscoveryStability|None = None
    visible_total_evidence: list[VisibleTotalEvidence] = Field(default_factory=list)
    visible_total_conflict: bool = False
    list_containers: list[ListContainer] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime|None = None
    elapsed_seconds: float = 0.0
    resolved_url: str|None = None
    failure_classification: str|None = None
    terminal_reason: str|None = None
    visited_nodes: list[dict[str,Any]] = Field(default_factory=list)
    actions_discovered: list[RecruitmentAction] = Field(default_factory=list)
    actions_attempted: list[ActionAttempt] = Field(default_factory=list)
    request_classifications: dict[str,int] = Field(default_factory=dict)
    terminal_candidates: list[TerminalCandidate] = Field(default_factory=list)
    terminal_activation: TerminalActivationRecord|None = None
    terminal_trace: TerminalActivationTrace|None = None
