<template>
  <div class="dashboard">
    <div class="page-header">
      <h1>仪表板</h1>
      <p>智能职业分类系统总览</p>
    </div>

    <!-- 系统状态卡片 -->
    <el-row :gutter="24" class="status-cards">
      <el-col :xs="24" :sm="12" :md="6">
        <el-card class="status-card">
          <div class="card-content">
            <div class="card-icon online">
              <el-icon size="32"><Connection /></el-icon>
            </div>
            <div class="card-info">
              <div class="card-title">系统状态</div>
              <div class="card-value" :class="{ online: systemStatus?.service === 'online' }">
                {{ systemStatus?.service === 'online' ? '在线' : '离线' }}
              </div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :sm="12" :md="6">
        <el-card class="status-card">
          <div class="card-content">
            <div class="card-icon">
              <el-icon size="32"><Cpu /></el-icon>
            </div>
            <div class="card-info">
              <div class="card-title">LLM提供商</div>
              <div class="card-value">{{ systemStatus?.llm_provider || 'N/A' }}</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :sm="12" :md="6">
        <el-card class="status-card">
          <div class="card-content">
            <div class="card-icon">
              <el-icon size="32"><Grid /></el-icon>
            </div>
            <div class="card-info">
              <div class="card-title">可用模板</div>
              <div class="card-value">{{ templates.length }}</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :sm="12" :md="6">
        <el-card class="status-card">
          <div class="card-content">
            <div class="card-icon">
              <el-icon size="32"><TrendCharts /></el-icon>
            </div>
            <div class="card-info">
              <div class="card-title">系统版本</div>
              <div class="card-value">{{ systemStatus?.version || 'N/A' }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 功能快捷入口 -->
    <el-row :gutter="24" class="quick-actions">
      <el-col :xs="24" :md="12">
        <el-card header="快速分类">
          <div class="quick-action-content">
            <el-icon size="48" color="#409EFF"><Document /></el-icon>
            <h3>单条职业分类</h3>
            <p>快速对单个职业进行智能分类分析</p>
            <el-button type="primary" @click="$router.push('/single-classification')">
              立即使用
            </el-button>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="12">
        <el-card header="批量处理">
          <div class="quick-action-content">
            <el-icon size="48" color="#67C23A"><FolderOpened /></el-icon>
            <h3>文件批量处理</h3>
            <p>上传Excel/CSV文件进行批量职业分类</p>
            <el-button type="success" @click="$router.push('/batch-processing')">
              立即使用
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 分类任务介绍 -->
    <el-card header="支持的分类任务" class="task-intro">
      <el-row :gutter="16">
        <el-col :xs="24" :md="8" v-for="task in taskIntros" :key="task.type">
          <div class="task-item">
            <div class="task-header">
              <el-icon size="24" :color="task.color">
                <component :is="task.icon" />
              </el-icon>
              <h4>{{ task.title }}</h4>
            </div>
            <p>{{ task.description }}</p>
            <div class="task-levels">
              <el-tag 
                v-for="level in task.levels" 
                :key="level" 
                size="small" 
                type="info"
              >
                {{ level }}
              </el-tag>
            </div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- 系统信息 -->
    <el-card header="系统信息" v-if="systemStatus">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="服务状态">
          <el-tag :type="systemStatus.service === 'online' ? 'success' : 'danger'">
            {{ systemStatus.service === 'online' ? '正常运行' : '服务异常' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="系统版本">
          {{ systemStatus.version }}
        </el-descriptions-item>
        <el-descriptions-item label="LLM提供商">
          {{ systemStatus.llm_provider }}
        </el-descriptions-item>
        <el-descriptions-item label="当前模型">
          {{ systemStatus.llm_model }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()

const systemStatus = computed(() => appStore.systemStatus)
const templates = computed(() => appStore.templates)

const taskIntros = [
  {
    type: 'risk_assessment',
    title: '职业风险评估',
    description: '基于作业环境和工作内容进行6级风险分类',
    icon: 'Warning',
    color: '#E6A23C',
    levels: ['1类', '2类', '3类', '4类', '5类', '6类']
  },
  {
    type: 'industry_classification',
    title: '行业分类',
    description: '基于国民经济行业分类标准进行20类行业归类',
    icon: 'OfficeBuilding',
    color: '#409EFF',
    levels: ['A类', 'B类', 'C类', '...', 'T类']
  },
  {
    type: 'skill_analysis',
    title: '技能分析',
    description: '基于工作复杂度和技能要求进行5级分析',
    icon: 'Trophy',
    color: '#67C23A',
    levels: ['初级', '中级', '高级', '专家级', '管理级']
  }
]

onMounted(() => {
  if (!systemStatus.value) {
    appStore.initApp()
  }
})
</script>

<style scoped>
.dashboard {
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

.status-cards {
  margin-bottom: 24px;
}

.status-card {
  margin-bottom: 16px;
}

.card-content {
  display: flex;
  align-items: center;
  gap: 16px;
}

.card-icon {
  width: 64px;
  height: 64px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f3f4f6;
  color: #6b7280;
}

.card-icon.online {
  background: #dcfce7;
  color: #16a34a;
}

.card-info {
  flex: 1;
}

.card-title {
  font-size: 14px;
  color: #6b7280;
  margin-bottom: 4px;
}

.card-value {
  font-size: 24px;
  font-weight: 600;
  color: #1f2937;
}

.card-value.online {
  color: #16a34a;
}

.quick-actions {
  margin-bottom: 24px;
}

.quick-action-content {
  text-align: center;
  padding: 20px 0;
}

.quick-action-content h3 {
  margin: 16px 0 8px 0;
  color: #1f2937;
}

.quick-action-content p {
  color: #6b7280;
  margin-bottom: 20px;
}

.task-intro {
  margin-bottom: 24px;
}

.task-item {
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  margin-bottom: 16px;
  height: 100%;
}

.task-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.task-header h4 {
  margin: 0;
  color: #1f2937;
}

.task-item p {
  color: #6b7280;
  margin-bottom: 16px;
  line-height: 1.5;
}

.task-levels {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

:deep(.el-card__header) {
  font-weight: 600;
  color: #1f2937;
}
</style>