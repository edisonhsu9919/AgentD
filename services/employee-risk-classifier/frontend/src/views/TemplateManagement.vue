<template>
  <div class="template-management">
    <div class="page-header">
      <h1>模板管理</h1>
      <p>管理分类任务模板，支持自定义分类标准</p>
    </div>

    <!-- 操作栏 -->
    <div class="action-bar">
      <el-button type="primary" @click="handleCreateTemplate">
        <el-icon><Plus /></el-icon>
        创建自定义模板
      </el-button>
      <el-button @click="handleRefresh" :loading="isLoading">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <!-- 模板列表 -->
    <el-row :gutter="24">
      <el-col 
        :xs="24" :sm="12" :md="8" :lg="6"
        v-for="template in templates" 
        :key="template.id"
      >
        <el-card class="template-card" :class="{ 'default-template': template.type === 'default' }">
          <template #header>
            <div class="card-header">
              <span class="template-name">{{ template.name }}</span>
              <el-tag 
                :type="template.type === 'default' ? 'primary' : 'success'"
                size="small"
              >
                {{ template.type === 'default' ? '系统' : '自定义' }}
              </el-tag>
            </div>
          </template>

          <div class="template-content">
            <p class="template-description">{{ template.description }}</p>
            
            <div class="template-levels">
              <div class="levels-header">
                <span>分类等级 ({{ template.levels.length }})</span>
              </div>
              <div class="levels-list">
                <el-tag 
                  v-for="level in template.levels.slice(0, 6)" 
                  :key="level.id"
                  size="small"
                  type="info"
                  class="level-tag"
                >
                  {{ level.id }}
                </el-tag>
                <span v-if="template.levels.length > 6" class="more-levels">
                  +{{ template.levels.length - 6 }}
                </span>
              </div>
            </div>
          </div>

          <template #footer>
            <div class="card-actions">
              <el-button size="small" @click="handleViewTemplate(template)">
                <el-icon><View /></el-icon>
                查看
              </el-button>
              <el-button 
                v-if="template.type === 'custom'"
                size="small" 
                type="primary"
                @click="handleEditTemplate(template)"
              >
                <el-icon><Edit /></el-icon>
                编辑
              </el-button>
              <el-button 
                v-if="template.type === 'custom'"
                size="small" 
                type="danger"
                @click="handleDeleteTemplate(template)"
              >
                <el-icon><Delete /></el-icon>
                删除
              </el-button>
            </div>
          </template>
        </el-card>
      </el-col>
    </el-row>

    <!-- 创建/编辑模板对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEditing ? '编辑模板' : '创建模板'"
      width="800px"
      :close-on-click-modal="false"
    >
      <el-form
        ref="formRef"
        :model="templateForm"
        :rules="templateRules"
        label-width="120px"
      >
        <el-form-item label="模板名称" prop="name" required>
          <el-input
            v-model="templateForm.name"
            placeholder="请输入模板名称"
            :maxlength="50"
            show-word-limit
          />
        </el-form-item>

        <el-form-item label="任务名称" prop="task_name" required>
          <el-input
            v-model="templateForm.task_name"
            placeholder="请输入任务名称"
            :maxlength="50"
            show-word-limit
          />
        </el-form-item>

        <el-form-item label="系统提示词" prop="system_prompt" required>
          <el-input
            v-model="templateForm.system_prompt"
            type="textarea"
            :rows="8"
            placeholder="请输入系统提示词，描述分类任务的要求和标准"
            :maxlength="2000"
            show-word-limit
          />
        </el-form-item>

        <el-form-item label="分类等级" required>
          <div class="classification-levels">
            <div 
              v-for="(level, index) in templateForm.classification_levels" 
              :key="index"
              class="level-item"
            >
              <div class="level-header">
                <span class="level-index">等级 {{ index + 1 }}</span>
                <el-button 
                  size="small" 
                  type="danger" 
                  text
                  @click="removeLevel(index)"
                  :disabled="templateForm.classification_levels.length <= 2"
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
              
              <el-row :gutter="12">
                <el-col :span="8">
                  <el-input
                    v-model="level.id"
                    placeholder="等级ID"
                    size="small"
                  />
                </el-col>
                <el-col :span="16">
                  <el-input
                    v-model="level.name"
                    placeholder="等级名称"
                    size="small"
                  />
                </el-col>
              </el-row>
              
              <el-input
                v-model="level.description"
                type="textarea"
                :rows="2"
                placeholder="等级描述"
                size="small"
                style="margin-top: 8px;"
              />
            </div>
            
            <el-button 
              type="primary" 
              dashed 
              @click="addLevel"
              style="width: 100%; margin-top: 12px;"
            >
              <el-icon><Plus /></el-icon>
              添加等级
            </el-button>
          </div>
        </el-form-item>

        <el-form-item label="输出格式" prop="output_format">
          <el-input
            v-model="templateForm.output_format"
            placeholder="输出格式模板，使用{classification}和{reason}占位符"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button 
          type="primary" 
          @click="handleSaveTemplate"
          :loading="isSaving"
        >
          {{ isEditing ? '更新' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 查看模板对话框 -->
    <el-dialog
      v-model="viewDialogVisible"
      title="模板详情"
      width="800px"
    >
      <el-descriptions :column="1" border v-if="viewingTemplate">
        <el-descriptions-item label="模板名称">
          {{ viewingTemplate.name }}
        </el-descriptions-item>
        <el-descriptions-item label="模板类型">
          <el-tag :type="viewingTemplate.type === 'default' ? 'primary' : 'success'">
            {{ viewingTemplate.type === 'default' ? '系统预设' : '用户自定义' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="模板描述">
          {{ viewingTemplate.description }}
        </el-descriptions-item>
        <el-descriptions-item label="分类等级">
          <div class="view-levels">
            <div 
              v-for="level in viewingTemplate.levels" 
              :key="level.id"
              class="view-level-item"
            >
              <el-tag type="info" size="small">{{ level.id }}</el-tag>
              <span class="level-name">{{ level.name }}</span>
              <p class="level-desc">{{ level.description }}</p>
            </div>
          </div>
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { classifierApi } from '@/api'
import type { TemplateInfo } from '@/types'

const appStore = useAppStore()
const formRef = ref<FormInstance>()

// 状态管理
const isLoading = ref(false)
const isSaving = ref(false)
const dialogVisible = ref(false)
const viewDialogVisible = ref(false)
const isEditing = ref(false)
const editingTemplateName = ref('')
const viewingTemplate = ref<TemplateInfo | null>(null)

// 模板表单
const templateForm = reactive({
  name: '',
  task_name: '',
  system_prompt: '',
  classification_levels: [
    { id: '', name: '', description: '' },
    { id: '', name: '', description: '' }
  ],
  output_format: '分类结果：{classification}\n理由：{reason}'
})

// 表单验证规则
const templateRules = {
  name: [
    { required: true, message: '请输入模板名称', trigger: 'blur' },
    { min: 2, max: 50, message: '模板名称长度应在2-50个字符之间', trigger: 'blur' }
  ],
  task_name: [
    { required: true, message: '请输入任务名称', trigger: 'blur' },
    { min: 2, max: 50, message: '任务名称长度应在2-50个字符之间', trigger: 'blur' }
  ],
  system_prompt: [
    { required: true, message: '请输入系统提示词', trigger: 'blur' },
    { min: 50, max: 2000, message: '系统提示词长度应在50-2000个字符之间', trigger: 'blur' }
  ]
}

// 计算属性
const templates = computed(() => appStore.templates)

// 刷新模板列表
async function handleRefresh() {
  isLoading.value = true
  try {
    await appStore.fetchTemplates()
    ElMessage.success('刷新成功')
  } catch (error) {
    ElMessage.error('刷新失败')
  } finally {
    isLoading.value = false
  }
}

// 创建模板
function handleCreateTemplate() {
  isEditing.value = false
  resetForm()
  dialogVisible.value = true
}

// 编辑模板
async function handleEditTemplate(template: TemplateInfo) {
  if (template.type !== 'custom') {
    ElMessage.warning('只能编辑自定义模板')
    return
  }
  
  try {
    isEditing.value = true
    editingTemplateName.value = template.id.startsWith('custom_') ? template.id.substring(7) : template.id
    
    // 获取模板详细信息
    const response = await classifierApi.getTemplateInfo(template.id)
    if (response.success) {
      const templateDetail = response.template
      
      // 填充表单
      templateForm.name = templateDetail.name
      templateForm.task_name = templateDetail.task_name
      templateForm.system_prompt = templateDetail.system_prompt || ''
      templateForm.output_format = templateDetail.output_format || '分类结果：{classification}\\n理由：{reason}'
      templateForm.classification_levels = templateDetail.levels.map((level: any) => ({
        id: level.id,
        name: level.name,
        description: level.description
      }))
      
      dialogVisible.value = true
    } else {
      ElMessage.error('获取模板详情失败')
    }
  } catch (error: any) {
    ElMessage.error(error.message || '获取模板详情失败')
  }
}

// 查看模板
function handleViewTemplate(template: TemplateInfo) {
  viewingTemplate.value = template
  viewDialogVisible.value = true
}

// 删除模板
async function handleDeleteTemplate(template: TemplateInfo) {
  try {
    await ElMessageBox.confirm(
      `确定要删除模板"${template.name}"吗？此操作不可撤销。`,
      '删除确认',
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
    
    // 提取自定义模板名称（移除custom_前缀）
    const templateName = template.id.startsWith('custom_') 
      ? template.id.substring(7) 
      : template.id
    
    await classifierApi.deleteCustomTemplate(templateName)
    await appStore.fetchTemplates()
    
    ElMessage.success('模板删除成功')
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || '删除失败')
    }
  }
}

// 保存模板
async function handleSaveTemplate() {
  if (!formRef.value) return
  
  try {
    await formRef.value.validate()
    
    // 验证分类等级
    const validLevels = templateForm.classification_levels.filter(
      level => level.id && level.name && level.description
    )
    
    if (validLevels.length < 2) {
      ElMessage.error('至少需要定义2个分类等级')
      return
    }
    
    isSaving.value = true
    
    const templateData = {
      name: templateForm.name,
      task_name: templateForm.task_name,
      system_prompt: templateForm.system_prompt,
      classification_levels: validLevels,
      output_format: templateForm.output_format
    }
    
    if (isEditing.value) {
      // 更新现有模板
      await classifierApi.updateCustomTemplate(editingTemplateName.value, templateData)
    } else {
      // 创建新模板
      await classifierApi.createCustomTemplate(templateData)
    }
    
    await appStore.fetchTemplates()
    
    dialogVisible.value = false
    ElMessage.success(`自定义模板${isEditing.value ? '更新' : '创建'}成功`)
  } catch (error: any) {
    ElMessage.error(error.message || `${isEditing.value ? '更新' : '创建'}失败`)
  } finally {
    isSaving.value = false
  }
}

// 添加分类等级
function addLevel() {
  templateForm.classification_levels.push({
    id: '',
    name: '',
    description: ''
  })
}

// 移除分类等级
function removeLevel(index: number) {
  if (templateForm.classification_levels.length > 2) {
    templateForm.classification_levels.splice(index, 1)
  }
}

// 重置表单
function resetForm() {
  isEditing.value = false
  editingTemplateName.value = ''
  templateForm.name = ''
  templateForm.task_name = ''
  templateForm.system_prompt = ''
  templateForm.classification_levels = [
    { id: '', name: '', description: '' },
    { id: '', name: '', description: '' }
  ]
  templateForm.output_format = '分类结果：{classification}\n理由：{reason}'
}

// 组件挂载
onMounted(() => {
  if (templates.value.length === 0) {
    appStore.fetchTemplates()
  }
})
</script>

<style scoped>
.template-management {
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

.action-bar {
  margin-bottom: 24px;
  display: flex;
  gap: 12px;
}

.template-card {
  margin-bottom: 16px;
  height: 320px;
  display: flex;
  flex-direction: column;
  transition: all 0.3s;
}

.template-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.default-template {
  border-left: 4px solid #409EFF;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.template-name {
  font-weight: 600;
  color: #1f2937;
}

.template-content {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.template-description {
  color: #6b7280;
  font-size: 14px;
  line-height: 1.5;
  margin-bottom: 16px;
  flex: 1;
}

.template-levels {
  margin-top: auto;
}

.levels-header {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 8px;
}

.levels-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.level-tag {
  margin: 0;
}

.more-levels {
  color: #6b7280;
  font-size: 12px;
}

.card-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.classification-levels {
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 16px;
  background: #fafafa;
}

.level-item {
  margin-bottom: 16px;
  padding: 12px;
  background: white;
  border-radius: 4px;
  border: 1px solid #e5e7eb;
}

.level-item:last-of-type {
  margin-bottom: 0;
}

.level-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.level-index {
  font-weight: 500;
  color: #374151;
  font-size: 14px;
}

.view-levels {
  max-height: 300px;
  overflow-y: auto;
}

.view-level-item {
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid #f3f4f6;
}

.view-level-item:last-child {
  border-bottom: none;
  margin-bottom: 0;
  padding-bottom: 0;
}

.level-name {
  margin-left: 8px;
  font-weight: 500;
  color: #1f2937;
}

.level-desc {
  margin: 8px 0 0 0;
  color: #6b7280;
  font-size: 14px;
  line-height: 1.5;
}

:deep(.el-card__header) {
  font-weight: 600;
  color: #1f2937;
}

:deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  height: 100%;
}

:deep(.el-card__footer) {
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid #f3f4f6;
}

:deep(.el-form-item__label) {
  font-weight: 500;
}

:deep(.el-descriptions__label) {
  font-weight: 500;
}

@media (max-width: 768px) {
  .action-bar {
    flex-direction: column;
  }
  
  .card-actions {
    flex-direction: column;
  }
  
  .levels-list {
    justify-content: flex-start;
  }
}
</style>