<template>
  <div class="batch-processing">
    <div class="page-header">
      <h1>批量文件处理 v2.0</h1>
      <p>上传Excel/CSV文件进行批量职业分类（支持智能列名匹配）</p>
    </div>

    <el-row :gutter="24">
      <!-- 文件上传区域 -->
      <el-col :xs="24" :lg="14">
        <el-card header="文件上传">
          <!-- 文件格式提示 -->
          <div class="upload-tips">
            <el-alert
              title="文件格式要求"
              type="info"
              :closable="false"
              show-icon
            >
              <template #default>
                <div>
                  <p>• 支持 .xlsx、.xls、.csv 格式文件，文件大小不超过10MB</p>
                  <p>• 必须包含<strong>岗位名称列</strong>（如：岗位名称、职位、工作等）</p>
                  <p>• 必须包含<strong>公司名称列</strong>（如：公司名称、企业名称、单位等）</p>
                  <p>• 分类结果将添加到表格的最后几列</p>
                </div>
                <el-button size="small" type="primary" link @click="downloadTemplate">
                  <el-icon><Download /></el-icon>
                  下载示例模板
                </el-button>
              </template>
            </el-alert>
          </div>
          
          <!-- 上传组件 -->
          <el-upload
            ref="uploadRef"
            class="upload-area"
            drag
            :auto-upload="false"
            :limit="1"
            :accept="'.xlsx,.xls,.csv'"
            :on-change="handleFileChange"
            :before-upload="beforeUpload"
            :file-list="fileList"
            :on-remove="handleRemove"
          >
            <el-icon class="el-icon--upload" size="48">
              <UploadFilled />
            </el-icon>
            <div class="el-upload__text">
              将Excel/CSV文件拖拽到此处，或<em>点击上传</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                支持 .xlsx、.xls、.csv 格式文件，文件大小不超过10MB
              </div>
            </template>
          </el-upload>

          <!-- 文件配置 -->
          <div class="file-config" v-if="fileList.length > 0">
            <el-divider>文件配置</el-divider>
            
            <el-form :model="uploadConfig" label-width="120px">
              <el-form-item label="岗位名称列">
                <el-select
                  v-model="uploadConfig.job_column"
                  placeholder="请选择岗位名称列"
                  style="width: 100%"
                  allow-create
                  filterable
                >
                  <el-option
                    v-for="header in previewHeaders"
                    :key="header"
                    :label="header"
                    :value="header"
                  />
                </el-select>
                <template #extra>
                  <div class="column-hint">
                    支持的列名：岗位名称、职位、工作、job、position等
                  </div>
                </template>
              </el-form-item>
              
              <el-form-item label="公司名称列" required>
                <el-select
                  v-model="uploadConfig.company_column"
                  placeholder="请选择公司名称列"
                  style="width: 100%"
                  allow-create
                  filterable
                >
                  <el-option
                    v-for="header in previewHeaders"
                    :key="header"
                    :label="header"
                    :value="header"
                  />
                </el-select>
                <template #extra>
                  <div class="column-hint">
                    支持的列名：公司名称、企业名称、单位、company等
                  </div>
                </template>
              </el-form-item>
              
              <el-form-item label="分类任务">
                <el-select 
                  v-model="uploadConfig.task_type" 
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
                  type="success" 
                  @click="handleStartProcessing"
                  :loading="isUploading"
                  :disabled="!canStartProcessing"
                  size="large"
                >
                  <el-icon><CircleCheck /></el-icon>
                  开始批量分类处理
                </el-button>
                <el-button @click="handleReset">重置</el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-card>

        <!-- 文件预览 -->
        <el-card header="文件预览" v-if="previewData.length > 0" class="preview-card">
          <div class="preview-info">
            <el-tag type="info">共 {{ previewData.length }} 行数据</el-tag>
            <el-tag type="warning" v-if="previewData.length > 5">仅显示前5行</el-tag>
            <el-tag 
              :type="uploadConfig.job_column && previewHeaders.includes(uploadConfig.job_column) ? 'success' : 'danger'"
            >
              岗位列: {{ uploadConfig.job_column || '未选择' }}
              <el-icon v-if="uploadConfig.job_column && previewHeaders.includes(uploadConfig.job_column)">
                <CircleCheck />
              </el-icon>
              <el-icon v-else>
                <CircleClose />
              </el-icon>
            </el-tag>
            <el-tag 
              :type="uploadConfig.company_column && previewHeaders.includes(uploadConfig.company_column) ? 'success' : 'danger'"
            >
              公司列: {{ uploadConfig.company_column || '未选择' }}
              <el-icon v-if="uploadConfig.company_column && previewHeaders.includes(uploadConfig.company_column)">
                <CircleCheck />
              </el-icon>
              <el-icon v-else>
                <CircleClose />
              </el-icon>
            </el-tag>
            <el-tag type="success" v-if="canStartProcessing">
              <el-icon><CircleCheck /></el-icon>
              配置完成，可以开始处理
            </el-tag>
          </div>
          
          <el-table :data="previewData.slice(0, 5)" border stripe>
            <el-table-column 
              v-for="(header, index) in previewHeaders" 
              :key="index"
              :prop="header"
              :label="header"
              :width="120"
              show-overflow-tooltip
            />
          </el-table>
        </el-card>
      </el-col>

      <!-- 处理状态和结果 -->
      <el-col :xs="24" :lg="10">
        <!-- 处理中 -->
        <el-card v-if="currentTask && currentTask.status === 'processing'" header="处理中">
          <div class="processing-content">
            <div class="processing-header">
              <el-icon class="is-loading" size="32" color="#409EFF">
                <Loading />
              </el-icon>
              <h3>正在处理文件...</h3>
            </div>
            
            <div class="processing-info">
              <el-descriptions :column="1" size="small">
                <el-descriptions-item label="文件名">
                  {{ currentTask.filename }}
                </el-descriptions-item>
                <el-descriptions-item label="原始总行数">
                  {{ currentTask.total_rows || 0 }}
                </el-descriptions-item>
                <el-descriptions-item label="去重后待处理">
                  {{ currentTask.unique_total_rows ?? currentTask.total_rows ?? 0 }}
                </el-descriptions-item>
                <el-descriptions-item label="去重已处理">
                  {{ currentTask.processed_unique_rows ?? currentTask.processed_rows ?? 0 }}
                </el-descriptions-item>
                <el-descriptions-item label="错误数">
                  {{ currentTask.error_rows || 0 }}
                </el-descriptions-item>
              </el-descriptions>
            </div>

            <el-progress 
              :percentage="Math.round(currentTask.progress * 100)"
              :stroke-width="12"
              :text-inside="true"
              color="#409EFF"
            />

            <div class="processing-actions">
              <el-button @click="handleCancelProcessing">取消处理</el-button>
            </div>
          </div>
        </el-card>

        <!-- 处理完成 -->
        <el-card v-else-if="currentTask && currentTask.status === 'completed'" header="处理完成">
          <div class="completed-content">
            <div class="completed-header">
              <el-icon size="32" color="#67C23A">
                <CircleCheck />
              </el-icon>
              <h3>文件处理完成</h3>
            </div>

            <el-descriptions :column="1" border>
              <el-descriptions-item label="文件名">
                {{ currentTask.filename }}
              </el-descriptions-item>
              <el-descriptions-item label="原始总行数">
                {{ currentTask.total_rows }}
              </el-descriptions-item>
              <el-descriptions-item label="去重后处理数">
                <el-tag type="info">{{ currentTask.unique_total_rows ?? currentTask.total_rows ?? 0 }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="成功处理">
                <el-tag type="success">{{ (currentTask.total_rows || 0) - (currentTask.error_rows || 0) }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="处理失败">
                <el-tag type="danger">{{ currentTask.error_rows || 0 }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="处理时间">
                {{ formatDuration(currentTask.created_at, currentTask.completed_at) }}
              </el-descriptions-item>
            </el-descriptions>

            <div class="completed-actions">
              <el-button type="primary" @click="handleDownload">
                <el-icon><Download /></el-icon>
                下载结果
              </el-button>
              <el-button @click="handleNewUpload">处理新文件</el-button>
            </div>
          </div>
        </el-card>

        <!-- 处理失败 -->
        <el-card v-else-if="currentTask && currentTask.status === 'failed'" header="处理失败">
          <div class="failed-content">
            <div class="failed-header">
              <el-icon size="32" color="#F56C6C">
                <CircleClose />
              </el-icon>
              <h3>文件处理失败</h3>
            </div>

            <el-alert
              :title="currentTask.error_message || '未知错误'"
              type="error"
              :closable="false"
              show-icon
            />

            <div class="failed-actions">
              <el-button type="primary" @click="handleRetry">重试处理</el-button>
              <el-button @click="handleNewUpload">重新上传</el-button>
            </div>
          </div>
        </el-card>

        <!-- 初始状态 -->
        <el-card v-else header="处理状态">
          <el-empty description="请上传文件开始批量处理">
            <el-icon size="64" color="#e0e0e0">
              <FolderOpened />
            </el-icon>
          </el-empty>
        </el-card>

        <!-- 历史任务 -->
        <el-card header="历史任务" v-if="taskHistory.length > 0" class="history-card">
          <div class="task-list">
            <div 
              v-for="task in taskHistory.slice(0, 5)" 
              :key="task.task_id"
              class="task-item"
              @click="handleViewTask(task)"
            >
              <div class="task-info">
                <div class="task-name">{{ task.filename }}</div>
                <div class="task-time">{{ formatTime(task.created_at) }}</div>
              </div>
              <div class="task-status">
                <el-tag 
                  :type="getTaskStatusType(task.status)"
                  size="small"
                >
                  {{ getTaskStatusText(task.status) }}
                </el-tag>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadInstance, UploadRawFile, UploadFile } from 'element-plus'
import { 
  CircleCheck, 
  CircleClose, 
  UploadFilled, 
  Upload, 
  Download, 
  Loading, 
  FolderOpened 
} from '@element-plus/icons-vue'
import { useAppStore } from '@/stores/app'
import { classifierApi } from '@/api'
import type { FileProcessingTask } from '@/types'
import * as XLSX from 'xlsx'

const appStore = useAppStore()
const uploadRef = ref<UploadInstance>()

// 上传配置
const uploadConfig = reactive({
  job_column: '岗位名称',
  company_column: '公司名称',
  task_type: 'risk_assessment'
})

// 状态管理  
const fileList = ref<UploadFile[]>([])
const isUploading = ref(false)
const currentTask = ref<FileProcessingTask | null>(null)
const taskHistory = ref<FileProcessingTask[]>([])
const previewData = ref<any[]>([])
const previewHeaders = ref<string[]>([])
const pollingTimer = ref<number | null>(null)

// 计算属性
const templates = computed(() => appStore.templates)

// 判断是否可以开始处理
const canStartProcessing = computed(() => {
  const hasFile = fileList.value.length > 0
  const hasJobColumn = !!uploadConfig.job_column
  const hasCompanyColumn = !!uploadConfig.company_column
  const jobColumnExists = previewHeaders.value.includes(uploadConfig.job_column)
  const companyColumnExists = previewHeaders.value.includes(uploadConfig.company_column)
  
  // 调试信息
  console.log('=== canStartProcessing debug ===')
  console.log('hasFile:', hasFile)
  console.log('hasJobColumn:', hasJobColumn) 
  console.log('hasCompanyColumn:', hasCompanyColumn)
  console.log('jobColumnExists:', jobColumnExists)
  console.log('companyColumnExists:', companyColumnExists)
  console.log('previewHeaders:', previewHeaders.value)
  console.log('jobColumn:', uploadConfig.job_column)
  console.log('companyColumn:', uploadConfig.company_column)
  console.log('fileList.length:', fileList.value.length)
  
  return hasFile && hasJobColumn && hasCompanyColumn && jobColumnExists && companyColumnExists
})

// 文件变化处理
function handleFileChange(file: UploadFile, fileListParam: UploadFile[]) {
  console.log('handleFileChange called:', file, fileListParam)
  
  // 更新fileList
  fileList.value = fileListParam
  
  if (file.raw) {
    handleFilePreview(file.raw)
  }
}

// 文件预览
async function handleFilePreview(file: File) {
  try {
    const reader = new FileReader()
    reader.onload = (e) => {
      const data = e.target?.result
      if (!data) return

      let workbook: XLSX.WorkBook
      let worksheet: XLSX.WorkSheet

      if (file.name.endsWith('.csv')) {
        workbook = XLSX.read(data, { type: 'binary' })
      } else {
        workbook = XLSX.read(data, { type: 'array' })
      }

      const sheetName = workbook.SheetNames[0]
      worksheet = workbook.Sheets[sheetName]
      const jsonData = XLSX.utils.sheet_to_json(worksheet, { header: 1 })

      if (jsonData.length > 0) {
        previewHeaders.value = jsonData[0] as string[]
        previewData.value = jsonData.slice(1).map((row: any) => {
          const obj: any = {}
          previewHeaders.value.forEach((header, index) => {
            obj[header] = row[index] || ''
          })
          return obj
        })
        
        // 智能匹配列名
        smartMatchColumns()
      }
    }

    if (file.name.endsWith('.csv')) {
      reader.readAsBinaryString(file)
    } else {
      reader.readAsArrayBuffer(file)
    }
  } catch (error) {
    console.error('File preview error:', error)
    ElMessage.error('文件预览失败')
  }
}

// 上传前检查
function beforeUpload(file: UploadRawFile) {
  const isValidFormat = ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
                        'application/vnd.ms-excel', 
                        'text/csv'].includes(file.type) ||
                        file.name.match(/\.(xlsx|xls|csv)$/i)
  
  if (!isValidFormat) {
    ElMessage.error('请上传Excel或CSV格式的文件')
    return false
  }

  const isValidSize = file.size / 1024 / 1024 < 10
  if (!isValidSize) {
    ElMessage.error('文件大小不能超过10MB')
    return false
  }

  return true
}

// 移除文件
function handleRemove() {
  previewData.value = []
  previewHeaders.value = []
  // 重置列名配置到默认值
  uploadConfig.job_column = '岗位名称'
  uploadConfig.company_column = '公司名称'
}

// 智能匹配列名
function smartMatchColumns() {
  const headers = previewHeaders.value
  
  // 岗位名称匹配
  const jobKeywords = ['岗位', '职位', '工作', '职业', '职务', 'job', 'position', 'title', 'role']
  let jobMatch = headers.find(header => 
    jobKeywords.some(keyword => header.toLowerCase().includes(keyword.toLowerCase()))
  )
  if (jobMatch) {
    uploadConfig.job_column = jobMatch
  }
  
  // 公司名称匹配
  const companyKeywords = ['公司', '企业', '单位', '机构', '组织', 'company', 'corp', 'inc', 'ltd']
  let companyMatch = headers.find(header => 
    companyKeywords.some(keyword => header.toLowerCase().includes(keyword.toLowerCase()))
  )
  if (companyMatch) {
    uploadConfig.company_column = companyMatch
  }
  // 如果没有匹配到，保持默认值，不设为空
}

// 开始批量分类处理
async function handleStartProcessing() {
  // 最终验证
  if (!canStartProcessing.value) {
    ElMessage.error('请先选择文件并正确配置岗位名称列和公司名称列')
    return
  }

  try {
    isUploading.value = true
    
    const file = fileList.value[0].raw!
    const response = await classifierApi.uploadFile(file, {
      job_column: uploadConfig.job_column,
      company_column: uploadConfig.company_column,
      task_type: uploadConfig.task_type
    })
    
    if (response.success) {
      currentTask.value = response.task
      
      // 添加到历史记录
      taskHistory.value.unshift(response.task)
      saveTaskHistory()
      
      // 开始轮询状态
      startPolling()
      
      ElMessage.success('文件上传成功，开始处理')
    } else {
      ElMessage.error('文件上传失败')
    }
  } catch (error: any) {
    ElMessage.error(error.message || '上传失败')
  } finally {
    isUploading.value = false
  }
}

// 开始轮询任务状态
function startPolling() {
  if (!currentTask.value) return
  
  pollingTimer.value = window.setInterval(async () => {
    if (!currentTask.value) return
    
    try {
      const response = await classifierApi.getFileStatus(currentTask.value.task_id)
      if (response.success) {
        currentTask.value = response.task
        
        // 更新历史记录
        const historyIndex = taskHistory.value.findIndex(t => t.task_id === response.task.task_id)
        if (historyIndex >= 0) {
          taskHistory.value[historyIndex] = response.task
          saveTaskHistory()
        }
        
        // 如果任务完成或失败，停止轮询
        if (response.task.status === 'completed' || response.task.status === 'failed') {
          stopPolling()
          
          if (response.task.status === 'completed') {
            ElMessage.success('文件处理完成')
          } else {
            ElMessage.error('文件处理失败')
          }
        }
      }
    } catch (error) {
      console.error('Polling error:', error)
    }
  }, 2000)
}

// 停止轮询
function stopPolling() {
  if (pollingTimer.value) {
    clearInterval(pollingTimer.value)
    pollingTimer.value = null
  }
}

// 取消处理
async function handleCancelProcessing() {
  try {
    await ElMessageBox.confirm('确定要取消当前处理吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    
    stopPolling()
    currentTask.value = null
    ElMessage.success('已取消处理')
  } catch {
    // 用户取消
  }
}

// 下载结果
async function handleDownload() {
  if (!currentTask.value?.task_id) return
  
  try {
    const blob = await classifierApi.downloadFile(currentTask.value.task_id)
    
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `classified_${currentTask.value.filename}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    
    ElMessage.success('文件下载成功')
  } catch (error: any) {
    ElMessage.error(error.message || '下载失败')
  }
}

// 重试处理
function handleRetry() {
  if (fileList.value.length > 0) {
    handleUpload()
  } else {
    ElMessage.error('请重新选择文件')
  }
}

// 处理新文件
function handleNewUpload() {
  handleReset()
  currentTask.value = null
}

// 重置表单
function handleReset() {
  uploadRef.value?.clearFiles()
  fileList.value = []
  previewData.value = []
  previewHeaders.value = []
}

// 查看历史任务
function handleViewTask(task: FileProcessingTask) {
  currentTask.value = task
  
  if (task.status === 'processing') {
    startPolling()
  }
}

// 获取任务状态类型
function getTaskStatusType(status: string) {
  switch (status) {
    case 'completed': return 'success'
    case 'processing': return 'warning'  
    case 'failed': return 'danger'
    default: return 'info'
  }
}

// 获取任务状态文本
function getTaskStatusText(status: string) {
  switch (status) {
    case 'pending': return '等待中'
    case 'processing': return '处理中'
    case 'completed': return '已完成'
    case 'failed': return '失败'
    default: return '未知'
  }
}

// 格式化时间
function formatTime(dateStr: string) {
  return new Date(dateStr).toLocaleString()
}

// 格式化持续时间
function formatDuration(startStr: string, endStr?: string) {
  if (!endStr) return '处理中...'
  
  const start = new Date(startStr).getTime()
  const end = new Date(endStr).getTime()
  const duration = Math.round((end - start) / 1000)
  
  if (duration < 60) {
    return `${duration}秒`
  } else if (duration < 3600) {
    return `${Math.floor(duration / 60)}分${duration % 60}秒`
  } else {
    const hours = Math.floor(duration / 3600)
    const minutes = Math.floor((duration % 3600) / 60)
    return `${hours}小时${minutes}分钟`
  }
}

// 保存任务历史
function saveTaskHistory() {
  localStorage.setItem('task_history', JSON.stringify(taskHistory.value.slice(0, 20)))
}

// 加载任务历史
function loadTaskHistory() {
  const saved = localStorage.getItem('task_history')
  if (saved) {
    try {
      taskHistory.value = JSON.parse(saved)
    } catch (e) {
      console.error('Failed to parse task history:', e)
    }
  }
}

// 下载示例模板
function downloadTemplate() {
  // 创建示例数据
  const templateData = [
    ['岗位名称', '公司名称', '工作地点', '薪资范围'],
    ['软件工程师', 'ABC科技有限公司', '北京', '15-25K'],
    ['产品经理', 'XYZ互联网公司', '上海', '20-30K'],
    ['UI设计师', '设计工作室', '深圳', '12-18K'],
    ['数据分析师', '大数据公司', '杭州', '18-28K'],
    ['运营专员', '电商平台', '广州', '8-15K']
  ]
  
  // 创建工作簿
  const worksheet = XLSX.utils.aoa_to_sheet(templateData)
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, '示例数据')
  
  // 下载文件
  XLSX.writeFile(workbook, '批量分类示例模板.xlsx')
  ElMessage.success('示例模板下载成功')
}

// 组件挂载
onMounted(() => {
  loadTaskHistory()
  
  if (templates.value.length === 0) {
    appStore.initApp()
  }
})

// 组件卸载
onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.batch-processing {
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

.upload-area {
  margin-bottom: 24px;
}

:deep(.el-upload-dragger) {
  width: 100%;
  height: 200px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border: 2px dashed #d1d5db;
  border-radius: 8px;
  background: #fafafa;
  transition: all 0.3s;
}

:deep(.el-upload-dragger:hover) {
  border-color: #409EFF;
  background: #f0f9ff;
}

.file-config {
  margin-top: 24px;
}

.template-option {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.preview-card {
  margin-top: 16px;
}

.preview-info {
  margin-bottom: 16px;
  display: flex;
  gap: 8px;
}

.processing-content,
.completed-content,
.failed-content {
  text-align: center;
  padding: 20px 0;
}

.processing-header,
.completed-header,
.failed-header {
  margin-bottom: 24px;
}

.processing-header h3,
.completed-header h3,
.failed-header h3 {
  margin: 12px 0 0 0;
  color: #1f2937;
}

.processing-info {
  margin-bottom: 24px;
  text-align: left;
}

.processing-actions,
.completed-actions,
.failed-actions {
  margin-top: 24px;
  display: flex;
  gap: 12px;
  justify-content: center;
}

.history-card {
  margin-top: 16px;
}

.task-list {
  max-height: 300px;
  overflow-y: auto;
}

.task-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.3s;
}

.task-item:hover {
  background: #f9fafb;
  border-color: #d1d5db;
}

.task-info {
  flex: 1;
  text-align: left;
}

.task-name {
  font-weight: 500;
  color: #1f2937;
  margin-bottom: 4px;
}

.task-time {
  font-size: 12px;
  color: #6b7280;
}

.task-status {
  flex-shrink: 0;
}

:deep(.el-card__header) {
  font-weight: 600;
  color: #1f2937;
}

:deep(.el-descriptions__label) {
  font-weight: 500;
}

.column-hint {
  font-size: 12px;
  color: #6b7280;
  margin-top: 4px;
}

.upload-tips {
  margin-bottom: 16px;
}

.upload-tips p {
  margin: 4px 0;
  font-size: 14px;
}

@media (max-width: 768px) {
  .processing-actions,
  .completed-actions,
  .failed-actions {
    flex-direction: column;
  }
  
  .task-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
</style>
