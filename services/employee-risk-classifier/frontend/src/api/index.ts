import axios from 'axios'
import type {
  SingleClassificationRequest,
  SingleClassificationResponse,
  BatchClassificationResponse,
  FileProcessingTask,
  TemplateInfo,
  LLMConfig,
  LLMProfile,
  SystemStatus
} from '@/types'

// 根据环境设置API基础URL
const getBaseURL = () => {
  // 开发环境使用代理
  if (import.meta.env.DEV) {
    return '/api'
  }
  
  // 生产环境直接连接后端
  const currentHost = window.location.hostname
  return `http://${currentHost}:8010/api`
}

// 创建axios实例
const api = axios.create({
  baseURL: getBaseURL(),
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 响应拦截器
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

// API接口定义
export const classifierApi = {
  // 健康检查
  health(): Promise<{ success: boolean; status: SystemStatus }> {
    return api.get('/health')
  },

  // 单条分类
  classifySingle(data: SingleClassificationRequest): Promise<SingleClassificationResponse> {
    return api.post('/classification/single', data)
  },

  // 批量分类
  classifyBatch(items: SingleClassificationRequest[]): Promise<BatchClassificationResponse> {
    return api.post('/classification/batch', { items })
  },

  // 文件上传
  uploadFile(
    file: File,
    options: {
      job_column?: string
      company_column?: string
      task_type?: string
      custom_template?: string
    } = {}
  ): Promise<{ success: boolean; task: FileProcessingTask }> {
    const formData = new FormData()
    formData.append('file', file)
    
    Object.entries(options).forEach(([key, value]) => {
      if (value) formData.append(key, value)
    })

    return api.post('/file/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  },

  // 获取文件处理状态
  getFileStatus(taskId: string): Promise<{ success: boolean; task: FileProcessingTask }> {
    return api.get(`/file/status/${taskId}`)
  },

  // 下载处理结果
  downloadFile(taskId: string): Promise<Blob> {
    return api.get(`/file/download/${taskId}`, {
      responseType: 'blob'
    })
  },

  // 获取模板列表
  getTemplates(): Promise<{ success: boolean; templates: TemplateInfo[] }> {
    return api.get('/templates')
  },

  // 获取单个模板详情
  getTemplateInfo(templateId: string): Promise<{ success: boolean; template: any }> {
    return api.get(`/templates/${templateId}`)
  },

  // 创建自定义模板
  createCustomTemplate(data: {
    name: string
    task_name: string
    system_prompt: string
    classification_levels: Array<{
      id: string
      name: string
      description: string
      risk_characteristics?: string[]
      typical_jobs?: string[]
    }>
    output_format?: string
  }): Promise<{ success: boolean; template: TemplateInfo }> {
    return api.post('/templates/custom', data)
  },

  // 更新自定义模板
  updateCustomTemplate(name: string, data: {
    name: string
    task_name: string
    system_prompt: string
    classification_levels: Array<{
      id: string
      name: string
      description: string
      risk_characteristics?: string[]
      typical_jobs?: string[]
    }>
    output_format?: string
  }): Promise<{ success: boolean; template: TemplateInfo }> {
    return api.put(`/templates/custom/${name}`, data)
  },

  // 删除自定义模板
  deleteCustomTemplate(name: string): Promise<{ success: boolean; message: string }> {
    return api.delete(`/templates/custom/${name}`)
  },

  // 获取LLM配置
  getLLMConfig(): Promise<{ success: boolean; config: LLMConfig }> {
    return api.get('/llm/config')
  },

  // 更新LLM配置
  updateLLMConfig(
    config: Partial<LLMConfig> & { profile_name?: string; set_active?: boolean }
  ): Promise<{ success: boolean; config: LLMConfig }> {
    return api.post('/llm/config', config)
  },

  // 获取LLM配置档案列表
  getLLMProfiles(): Promise<{ success: boolean; profiles: LLMProfile[]; active_profile: string }> {
    return api.get('/llm/config/profiles')
  },

  // 保存LLM配置档案
  saveLLMProfile(
    name: string,
    config: Partial<LLMConfig>,
    setActive = false
  ): Promise<{ success: boolean; config: LLMConfig }> {
    return api.post('/llm/config/profiles', {
      name,
      config,
      set_active: setActive
    })
  },

  // 激活LLM配置档案
  activateLLMProfile(name: string): Promise<{ success: boolean; config: LLMConfig }> {
    return api.post(`/llm/config/profiles/${name}/activate`)
  },

  // 删除LLM配置档案
  deleteLLMProfile(name: string): Promise<{ success: boolean; message: string }> {
    return api.delete(`/llm/config/profiles/${name}`)
  },

  // 真实测试LLM连通性
  testLLM(config?: Partial<LLMConfig>): Promise<{
    success: boolean
    test_success: boolean
    provider?: string
    model?: string
    response?: string
    error?: string
  }> {
    return api.post('/llm/test', config ? { config } : {})
  }
}

export default api
