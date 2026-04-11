import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { classifierApi } from '@/api'
import type { SystemStatus, TemplateInfo, LLMConfig } from '@/types'

export const useAppStore = defineStore('app', () => {
  // 系统状态
  const systemStatus = ref<SystemStatus | null>(null)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  // 模板列表
  const templates = ref<TemplateInfo[]>([])
  const selectedTemplate = ref<string>('risk_assessment')

  // LLM配置
  const llmConfig = ref<LLMConfig | null>(null)

  // 获取系统状态
  async function fetchSystemStatus() {
    try {
      isLoading.value = true
      error.value = null
      const response = await classifierApi.health()
      if (response.success) {
        systemStatus.value = response.status
      }
    } catch (err: any) {
      error.value = err.message || '获取系统状态失败'
      console.error('Failed to fetch system status:', err)
    } finally {
      isLoading.value = false
    }
  }

  // 获取模板列表
  async function fetchTemplates() {
    try {
      isLoading.value = true
      const response = await classifierApi.getTemplates()
      if (response.success) {
        templates.value = response.templates
      }
    } catch (err: any) {
      error.value = err.message || '获取模板列表失败'
      console.error('Failed to fetch templates:', err)
    } finally {
      isLoading.value = false
    }
  }

  // 获取LLM配置
  async function fetchLLMConfig() {
    try {
      const response = await classifierApi.getLLMConfig()
      if (response.success) {
        llmConfig.value = response.config
      }
    } catch (err: any) {
      error.value = err.message || '获取LLM配置失败'
      console.error('Failed to fetch LLM config:', err)
    }
  }

  // 更新LLM配置
  async function updateLLMConfig(config: Partial<LLMConfig> & { profile_name?: string; set_active?: boolean }) {
    try {
      isLoading.value = true
      const response = await classifierApi.updateLLMConfig(config)
      if (response.success) {
        llmConfig.value = response.config
        return true
      }
      return false
    } catch (err: any) {
      error.value = err.message || '更新LLM配置失败'
      console.error('Failed to update LLM config:', err)
      return false
    } finally {
      isLoading.value = false
    }
  }

  // 清除错误
  function clearError() {
    error.value = null
  }

  // 初始化应用
  async function initApp() {
    await Promise.all([
      fetchSystemStatus(),
      fetchTemplates(),
      fetchLLMConfig()
    ])
  }

  return {
    systemStatus,
    isLoading,
    error,
    templates,
    selectedTemplate,
    llmConfig,
    fetchSystemStatus,
    fetchTemplates,
    fetchLLMConfig,
    updateLLMConfig,
    clearError,
    initApp
  }
})
