// API响应类型
export interface ApiResponse<T = any> {
  success: boolean
  data?: T
  message?: string
  error?: string
}

// 分类任务类型
export type TaskType = 'risk_assessment' | 'industry_classification' | 'skill_analysis' | string

// 分类结果
export interface ClassificationResult {
  job_title: string
  company_name: string
  classification: string
  reason: string
  confidence?: number
  processing_time?: number
}

// 单条分类请求
export interface SingleClassificationRequest {
  job_title: string
  company_name?: string
  task_type: TaskType
  custom_template?: string
}

// 单条分类响应
export interface SingleClassificationResponse {
  success: boolean
  result?: ClassificationResult
  error?: string
  task_type: string
  template_used: string
}

// 批量分类响应
export interface BatchClassificationResponse {
  success: boolean
  results: ClassificationResult[]
  errors: Array<{
    index?: number
    job_title?: string
    error: string
  }>
  total_count: number
  success_count: number
  error_count: number
  processing_time: number
  task_type: string
}

// 文件处理任务
export interface FileProcessingTask {
  task_id: string
  filename: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  progress: number
  total_rows?: number
  processed_rows?: number
  unique_total_rows?: number
  processed_unique_rows?: number
  error_rows?: number
  created_at: string
  completed_at?: string
  download_url?: string
  error_message?: string
}

// 模板信息
export interface TemplateInfo {
  id: string
  name: string
  description: string
  type: 'default' | 'custom'
  levels: Array<{
    id: string
    name: string
    description: string
  }>
}

// LLM配置
export interface LLMConfig {
  provider: string
  api_key: string
  base_url: string
  model: string
  temperature: number
  max_tokens: number
  timeout: number
  active_profile?: string
}

export interface LLMProfile {
  name: string
  config: LLMConfig
  is_active: boolean
}

// 系统状态
export interface SystemStatus {
  service: string
  version: string
  llm_provider: string
  llm_model: string
  uptime: string
}
