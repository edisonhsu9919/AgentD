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

export type SessionStatus = "idle" | "queued" | "running" | "waiting" | "error";

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
  loaded_skills: string[];
  created_at: string;
  updated_at: string;
}

// --- Message & Parts ---

export interface TextPart {
  type: "text";
  content: string;
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

export type Part = TextPart | ToolCallPart | ToolResultPart | CompactionPart | ErrorPart | ReasoningPart;

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
  | SSECompactionDone;

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
  phase: "running" | "permission_waiting" | "error" | null;
  last_message_seq: number;
  pending_permissions_count: number;
  resumable: boolean;
  last_error: string | null;
  updated_at: string;
  last_call_prompt_tokens: number | null;
  last_call_completion_tokens: number | null;
  context_window_limit: number | null;
  context_usage_ratio: number | null;
  last_compaction_at: string | null;
  compaction_count: number;
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
