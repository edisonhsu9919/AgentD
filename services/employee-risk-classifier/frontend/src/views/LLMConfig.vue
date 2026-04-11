<template>
  <div class="llm-config">
    <div class="page-header">
      <h1>LLM配置管理</h1>
      <p>配置和管理大语言模型连接参数</p>
    </div>

    <el-row :gutter="24">
      <el-col :xs="24" :lg="16">
        <el-card header="LLM配置">
          <el-form
            ref="formRef"
            :model="form"
            :rules="rules"
            label-width="120px"
            v-loading="isLoading"
          >
            <el-form-item label="提供商" prop="provider" required>
              <el-select
                v-model="form.provider"
                placeholder="请选择LLM提供商"
                style="width: 100%"
                @change="handleProviderChange"
              >
                <el-option
                  v-for="provider in providers"
                  :key="provider.value"
                  :label="provider.label"
                  :value="provider.value"
                >
                  <div class="provider-option">
                    <span>{{ provider.label }}</span>
                    <el-tag size="small" :type="provider.type">
                      {{ provider.category }}
                    </el-tag>
                  </div>
                </el-option>
              </el-select>
            </el-form-item>

            <el-form-item label="API密钥" prop="api_key" required>
              <el-input
                v-model="form.api_key"
                type="password"
                show-password
                placeholder="请输入API密钥"
                :maxlength="200"
              />
            </el-form-item>

            <el-form-item label="Base URL" prop="base_url" required>
              <el-input
                v-model="form.base_url"
                placeholder="请输入API基础URL"
                :maxlength="500"
              />
            </el-form-item>

            <el-form-item label="模型名称" prop="model" required>
              <el-input
                v-model="form.model"
                placeholder="请输入模型名称"
                :maxlength="200"
              />
              <template #extra>
                <div class="model-suggestions" v-if="modelSuggestions.length > 0">
                  <span>推荐模型：</span>
                  <el-tag
                    v-for="model in modelSuggestions"
                    :key="model"
                    size="small"
                    type="info"
                    @click="form.model = model"
                    style="cursor: pointer; margin-right: 8px; margin-top: 4px;"
                  >
                    {{ model }}
                  </el-tag>
                </div>
              </template>
            </el-form-item>

            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="温度参数" prop="temperature">
                  <el-slider
                    v-model="form.temperature"
                    :min="0"
                    :max="2"
                    :step="0.1"
                    :show-tooltip="true"
                    :format-tooltip="(val: number) => val.toFixed(1)"
                  />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="最大令牌数" prop="max_tokens">
                  <el-input-number
                    v-model="form.max_tokens"
                    :min="1"
                    :max="4096"
                    :step="100"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
            </el-row>

            <el-form-item label="超时时间(秒)" prop="timeout">
              <el-input-number
                v-model="form.timeout"
                :min="10"
                :max="300"
                :step="10"
                style="width: 100%"
              />
            </el-form-item>

            <el-form-item>
              <el-button
                type="primary"
                @click="handleSave"
                :loading="isSaving"
                size="large"
              >
                <el-icon><Check /></el-icon>
                保存当前档案
              </el-button>
              <el-button @click="handleSaveAsProfile" :loading="isSaving">
                <el-icon><Plus /></el-icon>
                另存为新档案
              </el-button>
              <el-button @click="handleTest" :loading="isTesting">
                <el-icon><Connection /></el-icon>
                测试连接
              </el-button>
              <el-button @click="handleReset">
                <el-icon><RefreshLeft /></el-icon>
                重置
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="8">
        <el-card header="配置档案">
          <el-form label-width="90px">
            <el-form-item label="当前档案">
              <el-tag type="success">{{ activeProfile || 'default' }}</el-tag>
            </el-form-item>
            <el-form-item label="切换档案">
              <el-select v-model="selectedProfile" style="width: 100%" placeholder="选择配置档案">
                <el-option
                  v-for="profile in profiles"
                  :key="profile.name"
                  :label="profile.name"
                  :value="profile.name"
                />
              </el-select>
              <div class="profile-actions">
                <el-button size="small" type="primary" @click="handleSwitchProfile">切换</el-button>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  :disabled="!selectedProfile"
                  @click="handleDeleteProfile(selectedProfile)"
                >
                  删除
                </el-button>
              </div>
            </el-form-item>
            <el-form-item label="新档案名">
              <el-input v-model="newProfileName" placeholder="例如: qwen-online-prod" />
            </el-form-item>
          </el-form>

          <el-divider>已保存档案</el-divider>
          <div class="profile-list">
            <div v-for="profile in profiles" :key="profile.name" class="profile-item">
              <span>{{ profile.name }}</span>
              <el-tag v-if="profile.is_active" size="small" type="success">当前</el-tag>
            </div>
          </div>
        </el-card>

        <el-card header="当前配置" v-if="currentConfig" class="test-result-card">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="提供商">
              <el-tag :type="getProviderTagType(currentConfig.provider)">
                {{ getProviderLabel(currentConfig.provider) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="模型">
              {{ currentConfig.model }}
            </el-descriptions-item>
            <el-descriptions-item label="Base URL">
              <el-link :href="currentConfig.base_url" target="_blank" type="primary" style="font-size: 12px;">
                {{ currentConfig.base_url }}
              </el-link>
            </el-descriptions-item>
            <el-descriptions-item label="温度参数">
              {{ currentConfig.temperature }}
            </el-descriptions-item>
            <el-descriptions-item label="最大令牌数">
              {{ currentConfig.max_tokens }}
            </el-descriptions-item>
            <el-descriptions-item label="超时时间">
              {{ currentConfig.timeout }}秒
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card header="连接测试" v-if="testResult" class="test-result-card">
          <div class="test-result">
            <div class="test-status">
              <el-icon
                :size="32"
                :color="testResult.success ? '#67C23A' : '#F56C6C'"
              >
                <component :is="testResult.success ? 'CircleCheck' : 'CircleClose'" />
              </el-icon>
              <span class="status-text" :class="{ success: testResult.success, error: !testResult.success }">
                {{ testResult.success ? '连接成功' : '连接失败' }}
              </span>
            </div>

            <el-descriptions :column="1" border size="small" v-if="testResult.success">
              <el-descriptions-item label="响应内容">
                {{ testResult.response || 'N/A' }}
              </el-descriptions-item>
              <el-descriptions-item label="提供商">
                {{ testResult.provider }}
              </el-descriptions-item>
              <el-descriptions-item label="模型">
                {{ testResult.model }}
              </el-descriptions-item>
            </el-descriptions>

            <el-alert
              v-else
              :title="testResult.error"
              type="error"
              :closable="false"
              show-icon
            />
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { classifierApi } from '@/api'
import type { LLMProfile } from '@/types'

const appStore = useAppStore()
const formRef = ref<FormInstance>()

const form = reactive({
  provider: 'local_vllm',
  api_key: 'vllm',
  base_url: 'http://localhost:8000/v1',
  model: '/root/autodl-tmp/models/Qwen3-8b-AWQ',
  temperature: 0.1,
  max_tokens: 512,
  timeout: 60
})

const rules = {
  provider: [{ required: true, message: '请选择LLM提供商', trigger: 'change' }],
  api_key: [
    { required: true, message: '请输入API密钥', trigger: 'blur' },
    { min: 1, max: 200, message: 'API密钥长度应在1-200个字符之间', trigger: 'blur' }
  ],
  base_url: [
    { required: true, message: '请输入Base URL', trigger: 'blur' },
    { type: 'url', message: '请输入有效的URL', trigger: 'blur' }
  ],
  model: [
    { required: true, message: '请输入模型名称', trigger: 'blur' },
    { min: 1, max: 200, message: '模型名称长度应在1-200个字符之间', trigger: 'blur' }
  ],
  temperature: [{ type: 'number', min: 0, max: 2, message: '温度参数应在0-2之间', trigger: 'change' }],
  max_tokens: [{ type: 'number', min: 1, max: 4096, message: '最大令牌数应在1-4096之间', trigger: 'change' }],
  timeout: [{ type: 'number', min: 10, max: 300, message: '超时时间应在10-300秒之间', trigger: 'change' }]
}

const isLoading = ref(false)
const isSaving = ref(false)
const isTesting = ref(false)
const testResult = ref<any>(null)
const profiles = ref<LLMProfile[]>([])
const selectedProfile = ref('')
const newProfileName = ref('')

const providers = [
  { value: 'local_vllm', label: '本地vLLM', category: '本地部署', type: 'success' },
  { value: 'openai', label: 'OpenAI', category: '在线服务', type: 'primary' },
  { value: 'qwen', label: '通义千问', category: '在线服务', type: 'warning' },
  { value: 'custom', label: '自定义API', category: '自定义', type: 'info' }
]

const currentConfig = computed(() => appStore.llmConfig)
const activeProfile = computed(() => {
  const profile = profiles.value.find((item) => item.is_active)
  return profile?.name || currentConfig.value?.active_profile || ''
})

const modelSuggestions = computed(() => {
  switch (form.provider) {
    case 'openai':
      return ['gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo']
    case 'qwen':
      return ['qwen-turbo', 'qwen-plus', 'qwen-max']
    case 'local_vllm':
      return ['/root/autodl-tmp/models/Qwen3-8b-AWQ', '/root/models/llama2-7b', '/root/models/chatglm3-6b']
    default:
      return []
  }
})

function configPayload() {
  return {
    provider: form.provider,
    api_key: form.api_key,
    base_url: form.base_url,
    model: form.model,
    temperature: form.temperature,
    max_tokens: form.max_tokens,
    timeout: form.timeout
  }
}

async function loadProfiles() {
  const response = await classifierApi.getLLMProfiles()
  if (response.success) {
    profiles.value = response.profiles
    selectedProfile.value = response.active_profile
  }
}

function handleProviderChange(provider: string) {
  switch (provider) {
    case 'local_vllm':
      form.api_key = 'vllm'
      form.base_url = 'http://localhost:8000/v1'
      form.model = '/root/autodl-tmp/models/Qwen3-8b-AWQ'
      break
    case 'openai':
      form.api_key = ''
      form.base_url = 'https://api.openai.com/v1'
      form.model = 'gpt-3.5-turbo'
      break
    case 'qwen':
      form.api_key = ''
      form.base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
      form.model = 'qwen-turbo'
      break
    case 'custom':
      form.api_key = ''
      form.base_url = ''
      form.model = ''
      break
  }
  testResult.value = null
}

async function handleSave() {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
    isSaving.value = true

    const success = await appStore.updateLLMConfig({
      ...configPayload(),
      profile_name: selectedProfile.value || activeProfile.value || 'default',
      set_active: true
    })

    if (success) {
      await appStore.fetchLLMConfig()
      await loadProfiles()
      ElMessage.success('配置保存成功')
      testResult.value = null
    } else {
      ElMessage.error('配置保存失败')
    }
  } catch {
    // ignore validation errors
  } finally {
    isSaving.value = false
  }
}

async function handleSaveAsProfile() {
  if (!formRef.value) return
  const name = newProfileName.value.trim()
  if (!name) {
    ElMessage.warning('请输入新档案名称')
    return
  }

  try {
    await formRef.value.validate()
    isSaving.value = true
    const response = await classifierApi.saveLLMProfile(name, configPayload(), true)
    if (response.success) {
      selectedProfile.value = name
      newProfileName.value = ''
      await appStore.fetchLLMConfig()
      await loadProfiles()
      ElMessage.success('已保存为新档案并切换')
    } else {
      ElMessage.error('保存新档案失败')
    }
  } catch (error: any) {
    ElMessage.error(error.message || '保存新档案失败')
  } finally {
    isSaving.value = false
  }
}

async function handleSwitchProfile() {
  if (!selectedProfile.value) {
    ElMessage.warning('请先选择配置档案')
    return
  }

  try {
    isLoading.value = true
    const response = await classifierApi.activateLLMProfile(selectedProfile.value)
    if (response.success) {
      await appStore.fetchLLMConfig()
      if (currentConfig.value) {
        Object.assign(form, currentConfig.value)
      }
      await loadProfiles()
      testResult.value = null
      ElMessage.success(`已切换到档案: ${selectedProfile.value}`)
    } else {
      ElMessage.error('切换档案失败')
    }
  } catch (error: any) {
    ElMessage.error(error.message || '切换档案失败')
  } finally {
    isLoading.value = false
  }
}

async function handleDeleteProfile(name: string) {
  if (!name) return
  try {
    await ElMessageBox.confirm(`确定删除配置档案 "${name}" 吗？`, '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })

    const response = await classifierApi.deleteLLMProfile(name)
    if (response.success) {
      await appStore.fetchLLMConfig()
      if (currentConfig.value) {
        Object.assign(form, currentConfig.value)
      }
      await loadProfiles()
      ElMessage.success('档案删除成功')
    } else {
      ElMessage.error(response.message || '档案删除失败')
    }
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || '档案删除失败')
    }
  }
}

async function handleTest() {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
    isTesting.value = true
    testResult.value = null

    const testResp = await classifierApi.testLLM({
      ...configPayload(),
      profile_name: selectedProfile.value || activeProfile.value || 'default',
      set_active: true
    })

    testResult.value = {
      success: testResp.success && testResp.test_success,
      response: testResp.response || '',
      provider: testResp.provider || form.provider,
      model: testResp.model || form.model,
      error: testResp.error || null
    }

    if (testResult.value.success) {
      ElMessage.success('连接测试成功')
    } else {
      ElMessage.error(testResult.value.error || '连接测试失败')
    }
  } catch (error: any) {
    testResult.value = {
      success: false,
      error: error.message || '连接测试失败'
    }
    ElMessage.error('连接测试失败')
  } finally {
    isTesting.value = false
  }
}

async function handleReset() {
  try {
    await ElMessageBox.confirm('确定要重置配置吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    if (currentConfig.value) {
      Object.assign(form, currentConfig.value)
    }
    testResult.value = null
    ElMessage.success('配置已重置')
  } catch {
    // user canceled
  }
}

function getProviderTagType(provider: string) {
  const p = providers.find(item => item.value === provider)
  return p?.type || 'primary'
}

function getProviderLabel(provider: string) {
  const p = providers.find(item => item.value === provider)
  return p?.label || provider
}

onMounted(async () => {
  isLoading.value = true
  try {
    await appStore.fetchLLMConfig()
    await loadProfiles()
    if (currentConfig.value) {
      Object.assign(form, currentConfig.value)
      selectedProfile.value = currentConfig.value.active_profile || selectedProfile.value
    }
  } finally {
    isLoading.value = false
  }
})
</script>

<style scoped>
.llm-config {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h1 {
  margin: 0 0 8px 0;
  font-size: 28px;
  font-weight: 600;
  color: #1f2937;
}

.page-header p {
  margin: 0;
  color: #6b7280;
  font-size: 16px;
}

.provider-option {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.model-suggestions {
  margin-top: 8px;
  font-size: 12px;
  color: #6b7280;
}

.test-result-card {
  margin-top: 16px;
}

.test-result {
  text-align: center;
}

.test-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.status-text {
  font-size: 16px;
  font-weight: 600;
}

.status-text.success {
  color: #67C23A;
}

.status-text.error {
  color: #F56C6C;
}

.profile-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}

.profile-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.profile-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 8px 10px;
}

:deep(.el-card__header) {
  font-weight: 600;
  color: #1f2937;
}

:deep(.el-form-item__label) {
  font-weight: 500;
}

:deep(.el-descriptions__label) {
  font-weight: 500;
}

@media (max-width: 768px) {
  .test-status {
    flex-direction: row;
    justify-content: center;
  }
}
</style>
