// =============================================================================
// AgentD Frontend Types — strictly aligned with backend schema
// =============================================================================

// --- API Response Wrappers ---

export interface ApiResponse<T> {
  data: T;
  meta: null;
}

export interface ApiListResponse<T> {
  data: T[];
  meta: {
    total: number;
    page: number;
    page_size: number;
  };
}

export interface ApiError {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

// --- Auth ---

export interface User {
  id: string;
  username: string;
  role: "admin" | "user";
  workspace: string;
  is_active: boolean;
  department: string | null;
  employee_id: string | null;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface RefreshResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

// --- Session ---

export type SessionStatus = "idle" | "queued" | "running" | "waiting" | "error" | "subtask_waiting";

export interface TokenUsage {
  input: number;
  output: number;
  total: number;
}

export interface Session {
  id: string;
  user_id: string;
  title: string;
  agent_id: string;
  model_id: string;
  parent_id: string | null;
  status: SessionStatus;
  token_usage: TokenUsage;
  loaded_skills: Array<LoadedSkill | string>;
  created_at: string;
  updated_at: string;
}

// --- Message & Parts ---

export interface TextPart {
  type: "text";
  content: string;
}

export interface CommandPart {
  type: "command";
  command: string;
}

export interface CommandResultPart {
  type: "command_result";
  command: string;
  status: "success" | "error";
  text: string;
  skill_name?: string;
  skill_version?: string;
}

export interface ToolCallPart {
  type: "tool_call";
  tool_call_id: string;
  tool_name: string;
  input: Record<string, unknown>;
  status?: string;
}

export interface ToolResultPart {
  type: "tool_result";
  tool_call_id: string;
  output: string;
  is_error: boolean;
}

export interface CompactionPart {
  type: "compaction";
  summary: string;
  tokens_saved: number;
}

export interface ErrorPart {
  type: "error";
  message: string;
  code: string;
}

export interface ReasoningPart {
  type: "reasoning";
  content: string;
}

export interface SubtaskResultPart {
  type: "subtask_result";
  task_id: string;
  child_session_id: string;
  status: string;
  summary: string;
  artifact_root: string;
  result_ref: string;
  title: string;
}

export interface SourceRefItem {
  doc_id: string;
  title: string;
  kind: string;
  source_file: string;
  evidence_excerpt: string;
}

export interface SourceRefsPart {
  type: "source_refs";
  sources: SourceRefItem[];
}

export type Part = TextPart | CommandPart | CommandResultPart | ToolCallPart | ToolResultPart | CompactionPart | ErrorPart | ReasoningPart | SubtaskResultPart | SourceRefsPart;

export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "tool";
  parts: Part[];
  is_summary: boolean;
  token_usage: TokenUsage | null;
  seq: number;
  created_at: string;
}

export interface LoadedSkill {
  name: string;
  version: string;
}

export interface SessionCommandResponse {
  command: string;
  status: "success";
  message: Message;
  loaded_skills: LoadedSkill[];
}

// --- SSE Events ---

export interface SSETextDelta {
  event: "text_delta";
  session_id: string;
  message_id: string;
  content: string;
  timestamp: string;
}

export interface SSEToolStart {
  event: "tool_start";
  session_id: string;
  tool_call_id: string;
  tool_name: string;
  input: Record<string, unknown>;
  timestamp: string;
}

export interface SSEToolResult {
  event: "tool_result";
  session_id: string;
  tool_call_id: string;
  tool_name: string;
  output: string;
  is_error: boolean;
  timestamp: string;
}

export interface SSEPermissionAsk {
  event: "permission_ask";
  session_id: string;
  permission_id: string;
  tool_call_id: string;
  tool_name: string;
  input: Record<string, unknown>;
  timestamp: string;
}

export interface SSEPermissionResolved {
  event: "permission_resolved";
  session_id: string;
  permission_id: string;
  decision: "approved" | "denied";
  timestamp: string;
}

export interface SSEStatusChange {
  event: "status_change";
  session_id: string;
  status: SessionStatus;
  timestamp: string;
}

export interface SSETitleUpdate {
  event: "title_update";
  session_id: string;
  title: string;
  timestamp: string;
}

export interface SSEReasoningDelta {
  event: "reasoning_delta";
  session_id: string;
  content: string;
  timestamp: string;
}

export interface SSEDone {
  event: "done";
  session_id: string;
  token_usage: TokenUsage;
  timestamp: string;
}

export interface SSEError {
  event: "error";
  session_id: string;
  code: string;
  message: string;
  timestamp: string;
}

export interface SSEContextWarning {
  event: "context_warning";
  session_id: string;
  context_usage_ratio: number;
  timestamp: string;
}

export interface SSECompactionDone {
  event: "compaction_done";
  session_id: string;
  tokens_saved: number;
  timestamp: string;
}

export type SSEEvent =
  | SSETextDelta
  | SSEToolStart
  | SSEToolResult
  | SSEPermissionAsk
  | SSEPermissionResolved
  | SSEStatusChange
  | SSETitleUpdate
  | SSEReasoningDelta
  | SSEDone
  | SSEError
  | SSEContextWarning
  | SSECompactionDone
  | SSEPanelUpdate
  | SSEPanelSubmit
  | SSETaskStarted
  | SSETaskCompleted
  | SSETaskFailed;

// --- Workspace ---

export interface FileNode {
  path: string;
  name: string;
  type: "file" | "dir";
  size?: number;
  updated_at?: string;
  children?: FileNode[] | null;
}

export interface FileMeta {
  path: string;
  name: string;
  size: number;
  mime_type: string;
  extension: string;
  is_previewable: boolean;
  download_only: boolean;
  preview_mode: "text" | "image" | "pdf" | "office" | "binary" | "download" | null;
  updated_at: string;
  encoding: string | null;
}

// --- Runtime ---

export interface Runtime {
  session_id: string;
  status: SessionStatus;
  phase: "queued" | "running" | "permission_waiting" | "subtask_waiting" | "error" | null;
  last_message_seq: number;
  pending_permissions_count: number;
  resumable: boolean;
  last_error: string | null;
  updated_at: string;
  last_call_prompt_tokens: number | null;
  last_call_completion_tokens: number | null;
  context_window_limit: number | null;
  context_usage_ratio: number | null;
  runtime_state?: string | null;
  can_accept_user_prompt?: boolean;
  open_tool_call_ids?: string[];
  requires_human_input?: boolean;
  last_compaction_at: string | null;
  compaction_count: number;
  has_running_detached_tasks: boolean;
  running_detached_tasks_count: number;
}

// --- Policy ---

export type PolicyMode = "manual" | "autopilot" | "fsd";

export interface SessionPolicy {
  version: number;
  mode: PolicyMode;
  rules: unknown[];
}

// --- Prompt Response ---

export interface PromptResponse {
  message_id: string;
  run_id: string;
  status: string;
}

// --- Permission Record (from backend) ---

export interface PermissionRecord {
  id: string;
  session_id: string;
  tool_call_id: string;
  tool_name: string;
  input: Record<string, unknown>;
  status: string;
  created_at: string;
}

// --- Admin User Management ---

export interface CreateUserRequest {
  username: string;
  password: string;
  role?: "user" | "admin";
  is_active?: boolean;
}

export interface UpdateUserRequest {
  role?: "user" | "admin";
  is_active?: boolean;
  password?: string;
}

// --- User Profile & Skills (Phase H1/H2) ---

export interface UserSkillItem {
  name: string;
  version: string;
  is_enabled: boolean;
  usage_count: number;
  last_used_at: string | null;
  icon: string;
}

export interface UserProfile {
  id: string;
  username: string;
  role: "admin" | "user";
  workspace: string;
  is_active: boolean;
  department: string | null;
  employee_id: string | null;
  created_at: string;
  installed_skills: UserSkillItem[];
}

export interface AdminUserListItem {
  id: string;
  username: string;
  role: "admin" | "user";
  workspace: string;
  is_active: boolean;
  department: string | null;
  employee_id: string | null;
  created_at: string;
  installed_skill_count: number;
}

export interface SkillToggleResult {
  skill_name: string;
  is_enabled: boolean;
}

// --- Skill Square (Phase H3) ---

export interface SquareCardItem {
  name: string;
  description: string;
  icon: string;
  tags: string[];
  latest_version: string;
  available_versions: string[];
  usage_count_total: number;
  installed: boolean;
  installed_version: string | null;
  enabled: boolean | null;
}

export interface SquareTreeNode {
  name: string;
  path: string;
  type: "file" | "dir";
  children: SquareTreeNode[] | null;
}

export interface SquareVersionInfo {
  version: string;
  skill_id: string;
  created_at: string;
}

export interface SquareDetailResponse {
  name: string;
  description: string;
  icon: string;
  tags: string[];
  selected_version: string;
  versions: SquareVersionInfo[];
  installed: boolean;
  installed_version: string | null;
  enabled: boolean | null;
  selected_skill_id: string;
  readme_content: string;
  tree: SquareTreeNode[];
  usage_count_total: number;
}

// --- Task Plan (planning + todo_update subsystem) ---

export interface TaskPlanStep {
  id: string;
  status: "pending" | "in_progress" | "completed";
  title: string;
  detail: string;
}

export interface TaskPlan {
  active: boolean;
  task: {
    title: string;
    summary: string;
  };
  steps: TaskPlanStep[];
  updated_at?: string;
}

// --- Health Status (Phase I4) ---

export interface HealthRuntimeModel {
  name: string;
  provider_type: string;
  model_id: string;
  base_url_masked: string;
}

export interface HealthResponse {
  status: string;
  ready: boolean;
  degraded_reason: string | null;
  version: string;
  schema_version: string | null;
  schema_expected: string;
  schema_ok: boolean;
  runtime_model_source: string | null;
  runtime_model: HealthRuntimeModel | null;
  instance_id: string;
  started_at: string;
  pid: number;
}

// --- Model Config (Phase I2) ---

export interface ModelConfig {
  id: string;
  name: string;
  model_type: "llm" | "vlm";
  provider_type: string;
  base_url: string;
  api_key_masked: string;
  model_id: string;
  is_enabled: boolean;
  is_default: boolean;
  capabilities: Record<string, unknown> | null;
  timeout_seconds: number | null;
  context_window: number | null;
  extra_params: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ModelConfigCreate {
  name: string;
  model_type?: "llm" | "vlm";
  provider_type?: string;
  base_url: string;
  api_key?: string;
  model_id: string;
  is_enabled?: boolean;
  is_default?: boolean;
  capabilities?: Record<string, unknown> | null;
  timeout_seconds?: number | null;
  context_window?: number | null;
}

export interface ModelConfigUpdate {
  name?: string;
  model_type?: "llm" | "vlm";
  provider_type?: string;
  base_url?: string;
  api_key?: string;
  model_id?: string;
  is_enabled?: boolean;
  capabilities?: Record<string, unknown> | null;
  timeout_seconds?: number | null;
  context_window?: number | null;
}

// --- Runtime Model Config (Phase I2) ---

export interface RuntimeActiveConfig {
  source: string;
  name: string;
  base_url: string;
  api_key_masked: string;
  model_id: string;
  config_id?: string;
  context_window?: number | null;
}

export interface RuntimeModelConfigData {
  source: string;
  active_config: RuntimeActiveConfig;
  available_configs: Array<{
    id: string;
    name: string;
    model_id: string;
    is_enabled: boolean;
    is_default: boolean;
  }>;
}

// --- VLM Config (Phase O3) ---

export interface VLMActiveConfig {
  source: string;
  name: string;
  base_url: string;
  api_key_masked: string;
  model_id: string;
  supports_vision: boolean;
  supports_http_image_url: boolean;
  supports_data_uri_image: boolean;
  config_id?: string;
}

export interface VLMConfigResponse {
  available: boolean;
  source: string | null;
  active_config: VLMActiveConfig | null;
  message?: string;
}

// --- Diagnostics (Phase I4 + O3) ---

export interface DiagnosticsData {
  instance: {
    instance_id: string;
    pid: number;
    started_at: string;
    version: string;
  };
  schema: {
    version: string | null;
    expected: string;
    ok: boolean;
    db_reachable: boolean;
  };
  model: {
    source: string;
    name: string;
    model_id: string;
    base_url: string;
    api_key_masked: string;
    context_window: number | null;
  };
  vlm: {
    available: boolean;
    source?: string;
    name?: string;
    model_id?: string;
    base_url?: string;
    api_key_masked?: string;
    supports_vision?: boolean;
    supports_http_image_url?: boolean;
    supports_data_uri_image?: boolean;
  };
  config_summary: {
    total_configs: number;
    llm_configs: number;
    vlm_configs: number;
    enabled_configs: number;
    default_llm: string | null;
    default_vlm: string | null;
  };
  env_fallback: {
    local_llm_url: string;
    default_model_id: string;
    local_vlm_url: string;
    default_vlm_id: string;
    workspace_root: string;
    db_pool_size: number;
    db_max_overflow: number;
    debug: boolean;
  };
}

// --- Streaming state (frontend-only) ---

export interface StreamingToolCall {
  tool_call_id: string;
  tool_name: string;
  input: Record<string, unknown>;
  status: "running" | "completed" | "error";
  output?: string;
  is_error?: boolean;
}

export type StreamingTimelineItem =
  | {
      id: string;
      kind: "text";
      content: string;
      createdAt: number;
      updatedAt: number;
    }
  | {
      id: string;
      kind: "reasoning";
      content: string;
      createdAt: number;
      updatedAt: number;
    }
  | {
      id: string;
      kind: "tool";
      tool_call_id: string;
      tool_name: string;
      input: Record<string, unknown>;
      output?: string;
      is_error?: boolean;
      status: "running" | "completed" | "error";
      createdAt: number;
      updatedAt: number;
    };

// --- Task Instance (Phase P3) ---

export type TaskKind = "process" | "child_session";
export type TaskBlockingMode = "detached" | "blocking";
export type TaskStatus = "queued" | "running" | "waiting" | "completed" | "failed" | "cancelled";

export interface TaskInstance {
  task_id: string;
  session_id: string;
  task_kind: TaskKind;
  blocking_mode: TaskBlockingMode;
  status: TaskStatus;
  title: string;
  command: string;
  spawned_by_tool: string;
  tool_call_id: string;
  child_session_id: string | null;
  pid: number | null;
  artifact_root: string;
  stdout_path: string;
  stderr_path: string;
  error?: string | null;
  result_summary?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SSETaskStarted {
  event: "task_started";
  session_id: string;
  task_id: string;
  status: string;
  task_kind: TaskKind;
  child_session_id?: string;
  timestamp: string;
}

export interface SSETaskCompleted {
  event: "task_completed";
  session_id: string;
  task_id: string;
  status: "completed";
  returncode: number;
  timestamp: string;
}

export interface SSETaskFailed {
  event: "task_failed";
  session_id: string;
  task_id: string;
  status: "failed";
  returncode: number;
  timestamp: string;
}

// --- Knowledge Hub (Phase P6F) ---

export interface KnowledgeDocItem {
  doc_id: string;
  title: string;
  description: string;
  tags: string[];
  kind: string;
  permission: string;
  owner: string;
  source_file: string;
  created_at: string;
}

// --- Knowledge Sources (Phase P6) ---

export interface KnowledgeSourceRef {
  doc_id: string;
  title: string;
  kind: string;
  source_file: string;
  raw_available: boolean;
  knowledge_md_path: string;
  raw_path: string | null;
  evidence_excerpt?: string;
  page_hint?: string;
}

export interface KnowledgeSearchResult {
  doc_id: string;
  title: string;
  kind: string;
  match_count: number;
  excerpts: Array<{ line: number; text: string }>;
}

// --- Knowledge Import (Phase P6E) ---

export interface KnowledgeImportDraft {
  title: string;
  description: string;
  tags: string[];
  permission?: "public" | "private";
  kind: string;
  filename: string;
  file_size: number;
  limits: { description_max_chars: number };
}

export interface KnowledgeImportProgress {
  task_id: string;
  status: "extracting" | "committing" | "completed" | "failed";
  phase: string;
  filename: string;
  kind: string;
  title: string;
  source_path: string;
  raw_path: string;
  content_chars: number;
  doc_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

// --- Domain Extensions (v0.4.5 / Phase C1) ---

export interface DomainExtensionNav {
  label: string;
  href: string;
  order?: number;
}

export interface DomainExtensionFrontend {
  page_kind: "generic_extension";
  page_schema_endpoint: string;
}

export interface DomainExtensionItem {
  name: string;
  display_name: string;
  description?: string;
  version: string;
  status: "enabled" | "disabled" | "error";
  visibility: "all" | "admin";
  nav?: DomainExtensionNav;
  frontend?: DomainExtensionFrontend;
}

export interface DomainExtensionsResponse {
  extensions: DomainExtensionItem[];
}

export type ExtensionPageSchema =
  | ExtensionInfoPanelSchema
  | ExtensionSearchTableSchema;

export interface ExtensionInfoPanelSchema {
  kind: "info_panel";
  title: string;
  description?: string;
  cards?: Array<{
    title: string;
    value: string | number;
    description?: string;
  }>;
  actions?: Array<{
    label: string;
    href: string;
    variant?: "primary" | "secondary";
  }>;
}

export interface ExtensionSearchTableSchema {
  kind: "search_table";
  title: string;
  description?: string;
  search_placeholder?: string;
  columns: Array<{
    key: string;
    label: string;
  }>;
  rows: Array<Record<string, string | number | boolean | null>>;
}

export type ClauseImportJobStatus =
  | "uploaded"
  | "running"
  | "waiting_user"
  | "artifact_invalid"
  | "not_importable"
  | "extraction_failed"
  | "partial_artifacts_ready"
  | "extracted"
  | "reviewing"
  | "committing"
  | "committed"
  | "cancelled";

export interface ClauseImportJob {
  id: string;
  created_by: string | null;
  status: ClauseImportJobStatus;
  upload_root: string;
  artifact_root: string;
  import_session_id: string | null;
  agent_run_id: string | null;
  current_phase: string | null;
  summary: Record<string, unknown>;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
}

export interface ClauseImportItem {
  id: string;
  job_id: string;
  ordinal: number;
  clause_type: "main" | "additional" | string;
  name: string;
  normalized_name: string;
  category: string;
  is_active: boolean;
  content: string;
  applicable_main_clauses: string[];
  tags: string[];
  source_ref: Record<string, unknown>;
  metadata_json: Record<string, unknown>;
  validation_status: "valid" | "warning" | "error" | "duplicate_blocked" | string;
  review_status: "pending" | "approved" | "rejected" | string;
  duplicate_name_clause_id: string | null;
  duplicate_content_clause_id: string | null;
  warnings: Array<{ code?: string; message?: string }>;
  errors: Array<{ code?: string; message?: string }>;
  created_at: string | null;
  updated_at: string | null;
}

export interface ClauseRecord {
  id: string;
  clause_type: "main" | "additional" | string;
  name: string;
  normalized_name: string;
  category: string;
  is_active: boolean;
  applicable_main_clauses: string[];
  tags: string[];
  source_ref: Record<string, unknown>;
  metadata_json: Record<string, unknown>;
  content?: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ClauseImportMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  parts: Part[];
  seq: number;
  created_at: string | null;
}

// --- Panel System (Phase P1) ---

export type PanelType = "file_preview" | "task_output" | "html_app";

export interface StructuredContent {
  widget: "table" | "markdown" | "image" | "json";
  data: Record<string, unknown>;
}

export interface HtmlSandboxContent {
  html: string;
  height: number;
  permissions: string[];
  interaction_id: string;
  callback_task_id: string;
}

export interface PanelSubmitPayload {
  interaction_id: string;
  callback_task_id: string;
  data: Record<string, unknown>;
}

export interface SSEPanelSubmit {
  event: "panel_submit";
  session_id: string;
  interaction_id: string;
  callback_task_id: string;
  data: Record<string, unknown>;
  timestamp: string;
}

export interface PanelContent {
  version: string;
  type: "structured" | "html_sandbox";
  title: string;
  subtitle?: string | null;
  structured?: StructuredContent | null;
  html_sandbox?: HtmlSandboxContent | null;
}

export interface SSEPanelUpdate {
  event: "panel_update";
  session_id: string;
  panel_type: PanelType;
  panel_content: PanelContent;
  timestamp: string;
}

export interface InspectResult {
  path: string;
  kind: string;
  inspectable: boolean;
  // Common
  size_bytes?: number;
  mime_type?: string;
  preview_mode?: string;
  // PDF
  pdf_kind?: string;
  page_count?: number;
  extractable_text_ratio?: number;
  metadata?: Record<string, string | null>;
  text_sample?: string;
  // Office common
  office_kind?: string;
  // DOCX
  paragraph_count?: number;
  heading_count?: number;
  headings?: string[];
  table_count?: number;
  // XLSX
  sheet_count?: number;
  sheet_names?: string[];
  sheets?: Array<{
    name: string;
    dimensions: string;
    max_row: number;
    max_column: number;
    header_row: string[];
    sample_rows: string[][];
  }>;
  // PPTX
  slide_count?: number;
  slides?: Array<{
    number: number;
    title: string;
    text_preview: string;
    has_notes: boolean;
  }>;
  // EML
  email_kind?: string;
  subject?: string;
  from_addr?: string;
  to_addr?: string;
  date?: string;
  body_preview?: string;
  attachment_count?: number;
  attachments?: Array<{
    filename: string;
    content_type: string;
  }>;
  // Image
  image_format?: string;
  width?: number;
  height?: number;
}

// Panel tab state (frontend-only)
export interface PanelTab {
  id: string;
  type: PanelType;
  title: string;
  subtitle?: string;
  attention?: boolean;
}
