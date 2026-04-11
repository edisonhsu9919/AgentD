<template>
  <div class="single-classification">
    <div class="page-header">
      <h1>单条职业分类</h1>
      <p>快速对单个职业进行智能分类分析</p>
    </div>

    <el-row :gutter="24">
      <!-- 输入表单 -->
      <el-col :xs="24" :lg="12">
        <el-card header="职业信息输入">
          <el-form 
            ref="formRef"
            :model="form" 
            :rules="rules" 
            label-width="100px"
            @submit.prevent="handleSubmit"
          >
            <el-form-item label="岗位名称" prop="job_title" required>
              <el-input
                v-model="form.job_title"
                placeholder="请输入岗位名称，如：软件工程师"
                :maxlength="100"
                show-word-limit
                clearable
              />
            </el-form-item>

            <el-form-item label="公司名称" prop="company_name">
              <el-input
                v-model="form.company_name"
                placeholder="请输入公司名称（可选）"
                :maxlength="100"
                show-word-limit
                clearable
              />
            </el-form-item>

            <el-form-item label="分类任务" prop="task_type" required>
              <el-select 
                v-model="form.task_type" 
                placeholder="请选择分类任务"
                style="width: 100%"
              >
                <el-option
                  v-for="template in templates"
                  :key="template.id"
                  :label="template.name"
                  :value="template.id"
                >
                  <div class="template-option">
                    <span>{{ template.name }}</span>
                    <el-tag size="small" :type="template.type === 'default' ? 'primary' : 'success'">
                      {{ template.type === 'default' ? '系统' : '自定义' }}
                    </el-tag>
                  </div>
                </el-option>
              </el-select>
            </el-form-item>

            <el-form-item>
              <el-button 
                type="primary" 
                @click="handleSubmit"
                :loading="isLoading"
                :disabled="!form.job_title || !form.task_type"
                size="large"
                style="width: 100%"
              >
                <el-icon><Search /></el-icon>
                开始分类
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 分类任务信息 -->
        <el-card header="任务信息" v-if="selectedTemplateInfo" class="template-info">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="任务名称">
              {{ selectedTemplateInfo.name }}
            </el-descriptions-item>
            <el-descriptions-item label="任务类型">
              <el-tag :type="selectedTemplateInfo.type === 'default' ? 'primary' : 'success'">
                {{ selectedTemplateInfo.type === 'default' ? '系统预设' : '用户自定义' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="分类等级">
              <div class="classification-levels">
                <el-tag 
                  v-for="level in selectedTemplateInfo.levels.slice(0, 6)" 
                  :key="level.id"
                  size="small"
                  type="info"
                  style="margin-right: 8px; margin-bottom: 4px;"
                >
                  {{ level.id }}
                </el-tag>
                <span v-if="selectedTemplateInfo.levels.length > 6" class="more-levels">
                  +{{ selectedTemplateInfo.levels.length - 6 }}
                </span>
              </div>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>

      <!-- 分类结果 -->
      <el-col :xs="24" :lg="12">
        <!-- 加载状态 -->
        <el-card v-if="isLoading" header="正在分类中...">
          <div class="loading-content">
            <el-icon class="is-loading" size="48" color="#409EFF">
              <Loading />
            </el-icon>
            <p>AI正在分析职业信息，请稍候...</p>
            <el-progress 
              :percentage="loadingProgress" 
              :show-text="false" 
              stroke-width="8"
              color="#409EFF"
            />
          </div>
        </el-card>

        <!-- 分类结果 -->
        <el-card v-else-if="result && !error" header="分类结果">
          <div class="result-content">
            <!-- 分类结果标题 -->
            <div class="result-header">
              <div class="result-classification">
                <el-tag size="large" :type="getClassificationTagType(result.classification)">
                  {{ result.classification }}
                </el-tag>
                <span class="job-title">{{ result.job_title }}</span>
              </div>
              <div class="processing-time" v-if="result.processing_time">
                <el-icon><Timer /></el-icon>
                {{ result.processing_time.toFixed(2) }}秒
              </div>
            </div>

            <!-- 详细信息 -->
            <el-descriptions :column="1" border>
              <el-descriptions-item label="岗位名称">
                {{ result.job_title }}
              </el-descriptions-item>
              <el-descriptions-item label="公司名称" v-if="result.company_name">
                {{ result.company_name }}
              </el-descriptions-item>
              <el-descriptions-item label="分类结果">
                <el-tag size="large" :type="getClassificationTagType(result.classification)">
                  {{ result.classification }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="分类理由">
                <div class="classification-reason">
                  {{ result.reason }}
                </div>
              </el-descriptions-item>
              <el-descriptions-item label="处理时间" v-if="result.processing_time">
                {{ result.processing_time.toFixed(2) }} 秒
              </el-descriptions-item>
              <el-descriptions-item label="置信度" v-if="result.confidence">
                <el-progress 
                  :percentage="result.confidence * 100" 
                  :color="getConfidenceColor(result.confidence)"
                  :stroke-width="10"
                />
              </el-descriptions-item>
            </el-descriptions>

            <!-- 操作按钮 -->
            <div class="result-actions">
              <el-button @click="handleReset">重新分类</el-button>
              <el-button type="primary" @click="handleExport">导出结果</el-button>
            </div>
          </div>
        </el-card>

        <!-- 错误状态 -->
        <el-card v-else-if="error" header="分类失败">
          <el-alert
            :title="error"
            type="error"
            :closable="false"
            show-icon
          />
          <div class="error-actions">
            <el-button @click="handleRetry">重试</el-button>
            <el-button type="primary" @click="handleReset">重新输入</el-button>
          </div>
        </el-card>

        <!-- 初始状态 -->
        <el-card v-else header="分类结果">
          <el-empty description="请填写职业信息并开始分类">
            <el-icon size="64" color="#e0e0e0">
              <Document />
            </el-icon>
          </el-empty>
        </el-card>
      </el-col>
    </el-row>

    <!-- 历史记录 -->
    <el-card header="最近分类记录" v-if="history.length > 0" class="history-card">
      <el-table :data="history" stripe>
        <el-table-column prop="job_title" label="岗位名称" />
        <el-table-column prop="company_name" label="公司名称" />
        <el-table-column prop="classification" label="分类结果">
          <template #default="{ row }">
            <el-tag :type="getClassificationTagType(row.classification)">
              {{ row.classification }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="分类理由" show-overflow-tooltip />
        <el-table-column prop="timestamp" label="分类时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.timestamp) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row, $index }">
            <el-button size="small" @click="handleReuse(row)">重用</el-button>
            <el-button size="small" type="danger" @click="handleDeleteHistory($index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { classifierApi } from '@/api'
import type { SingleClassificationRequest, ClassificationResult } from '@/types'

const appStore = useAppStore()
const formRef = ref<FormInstance>()

// 表单数据
const form = reactive<SingleClassificationRequest>({
  job_title: '',
  company_name: '',
  task_type: 'risk_assessment'
})

// 表单验证规则
const rules = {
  job_title: [
    { required: true, message: '请输入岗位名称', trigger: 'blur' },
    { min: 2, max: 100, message: '岗位名称长度应在2-100个字符之间', trigger: 'blur' }
  ],
  company_name: [
    { max: 100, message: '公司名称不能超过100个字符', trigger: 'blur' }
  ],
  task_type: [
    { required: true, message: '请选择分类任务', trigger: 'change' }
  ]
}

// 状态管理
const isLoading = ref(false)
const loadingProgress = ref(0)
const result = ref<ClassificationResult | null>(null)
const error = ref<string | null>(null)
const history = ref<Array<ClassificationResult & { timestamp: number }>>([])

// 计算属性
const templates = computed(() => appStore.templates)
const selectedTemplateInfo = computed(() => {
  return templates.value.find(t => t.id === form.task_type)
})

// 监听加载状态，模拟进度条
watch(isLoading, (newVal) => {
  if (newVal) {
    loadingProgress.value = 0
    const timer = setInterval(() => {
      loadingProgress.value += Math.random() * 15
      if (loadingProgress.value >= 90) {
        clearInterval(timer)
      }
    }, 300)
  } else {
    loadingProgress.value = 100
  }
})

// 提交分类请求
async function handleSubmit() {
  if (!formRef.value) return
  
  try {
    await formRef.value.validate()
    
    isLoading.value = true
    error.value = null
    result.value = null
    
    const response = await classifierApi.classifySingle(form)
    
    if (response.success && response.result) {
      result.value = response.result
      
      // 添加到历史记录
      history.value.unshift({
        ...response.result,
        timestamp: Date.now()
      })
      
      // 只保留最近10条记录
      if (history.value.length > 10) {
        history.value = history.value.slice(0, 10)
      }
      
      // 保存到本地存储
      localStorage.setItem('classification_history', JSON.stringify(history.value))
      
      ElMessage.success('分类完成')
    } else {
      error.value = response.error || '分类失败'
      ElMessage.error(error.value)
    }
  } catch (err: any) {
    error.value = err.message || '分类请求失败'
    ElMessage.error(error.value)
  } finally {
    isLoading.value = false
  }
}

// 重试
function handleRetry() {
  error.value = null
  handleSubmit()
}

// 重置
function handleReset() {
  result.value = null
  error.value = null
  form.job_title = ''
  form.company_name = ''
}

// 导出结果
function handleExport() {
  if (!result.value) return
  
  const data = {
    岗位名称: result.value.job_title,
    公司名称: result.value.company_name || '',
    分类结果: result.value.classification,
    分类理由: result.value.reason,
    处理时间: result.value.processing_time?.toFixed(2) + '秒' || '',
    分类时间: new Date().toLocaleString()
  }
  
  const csv = Object.entries(data).map(([key, value]) => `${key},${value}`).join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `职业分类结果_${result.value.job_title}_${new Date().getTime()}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  
  ElMessage.success('结果已导出')
}

// 重用历史记录
function handleReuse(record: ClassificationResult) {
  form.job_title = record.job_title
  form.company_name = record.company_name || ''
  result.value = null
  error.value = null
}

// 删除历史记录
async function handleDeleteHistory(index: number) {
  try {
    await ElMessageBox.confirm('确定要删除这条记录吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    
    history.value.splice(index, 1)
    localStorage.setItem('classification_history', JSON.stringify(history.value))
    ElMessage.success('记录已删除')
  } catch {
    // 用户取消
  }
}

// 获取分类标签类型
function getClassificationTagType(classification: string) {
  if (classification.includes('1') || classification.includes('2') || classification.includes('3')) {
    return 'success'
  } else if (classification.includes('4')) {
    return 'warning'
  } else if (classification.includes('5')) {
    return 'danger'
  } else if (classification.includes('6')) {
    return 'info'
  }
  return 'primary'
}

// 获取置信度颜色
function getConfidenceColor(confidence: number) {
  if (confidence >= 0.8) return '#67C23A'
  if (confidence >= 0.6) return '#E6A23C'
  return '#F56C6C'
}

// 格式化时间
function formatTime(timestamp: number) {
  return new Date(timestamp).toLocaleString()
}

// 组件挂载时初始化
onMounted(() => {
  // 加载历史记录
  const savedHistory = localStorage.getItem('classification_history')
  if (savedHistory) {
    try {
      history.value = JSON.parse(savedHistory)
    } catch (e) {
      console.error('Failed to parse history:', e)
    }
  }
  
  // 如果模板列表为空，初始化应用
  if (templates.value.length === 0) {
    appStore.initApp()
  }
})
</script>

<style scoped>
.single-classification {
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

.template-option {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.template-info {
  margin-top: 16px;
}

.classification-levels {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.more-levels {
  color: #6b7280;
  font-size: 12px;
}

.loading-content {
  text-align: center;
  padding: 40px 20px;
}

.loading-content p {
  margin: 16px 0;
  color: #6b7280;
}

.result-content {
  min-height: 300px;
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e5e7eb;
}

.result-classification {
  display: flex;
  align-items: center;
  gap: 12px;
}

.job-title {
  font-size: 18px;
  font-weight: 600;
  color: #1f2937;
}

.processing-time {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #6b7280;
  font-size: 14px;
}

.classification-reason {
  line-height: 1.6;
  color: #374151;
}

.result-actions {
  margin-top: 24px;
  display: flex;
  gap: 12px;
  justify-content: center;
}

.error-actions {
  margin-top: 16px;
  text-align: center;
}

.history-card {
  margin-top: 24px;
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
  .result-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
  
  .result-actions {
    flex-direction: column;
  }
}
</style>