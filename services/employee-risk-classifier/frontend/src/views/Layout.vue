<template>
  <el-container class="layout-container">
    <!-- 头部 -->
    <el-header class="layout-header">
      <div class="header-left">
        <div class="logo">
          <img src="@/assets/logo.png" alt="分类通Logo" class="logo-image" />
          <span class="logo-text">分类通-AI分类助手</span>
        </div>
      </div>
      
      <div class="header-right">
        <el-badge 
          :value="systemStatus?.service === 'online' ? '' : '离线'" 
          :type="systemStatus?.service === 'online' ? 'success' : 'danger'"
          :hidden="systemStatus?.service === 'online'"
        >
          <el-button 
            :type="systemStatus?.service === 'online' ? 'success' : 'danger'" 
            size="small" 
            plain
            @click="appStore.fetchSystemStatus()"
          >
            <el-icon>
              <Connection />
            </el-icon>
            {{ systemStatus?.service === 'online' ? '在线' : '离线' }}
          </el-button>
        </el-badge>
        
        <el-dropdown trigger="click">
          <el-button type="primary" size="small">
            <el-icon><User /></el-icon>
            系统信息
            <el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item>
                <el-icon><Monitor /></el-icon>
                版本: {{ systemStatus?.version || 'N/A' }}
              </el-dropdown-item>
              <el-dropdown-item>
                <el-icon><Cpu /></el-icon>
                模型: {{ systemStatus?.llm_model || 'N/A' }}
              </el-dropdown-item>
              <el-dropdown-item divided @click="handleRefresh">
                <el-icon><Refresh /></el-icon>
                刷新状态
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>

    <el-container>
      <!-- 侧边栏 -->
      <el-aside :width="isCollapse ? '64px' : '200px'" class="layout-aside">
        <div class="aside-header">
          <el-button 
            type="text" 
            @click="toggleCollapse"
            class="collapse-btn"
          >
            <el-icon size="20">
              <component :is="isCollapse ? 'Expand' : 'Fold'" />
            </el-icon>
          </el-button>
        </div>
        
        <el-menu
          :default-active="$route.path"
          :collapse="isCollapse"
          :unique-opened="true"
          router
          class="layout-menu"
        >
          <el-menu-item 
            v-for="route in menuRoutes" 
            :key="route.path"
            :index="route.path"
          >
            <el-icon>
              <component :is="route.meta.icon" />
            </el-icon>
            <template #title>{{ route.meta.title }}</template>
          </el-menu-item>
        </el-menu>
      </el-aside>

      <!-- 主内容区 -->
      <el-main class="layout-main">
        <div class="main-content">
          <router-view />
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { ElMessage } from 'element-plus'

const route = useRoute()
const appStore = useAppStore()

const isCollapse = ref(false)

const systemStatus = computed(() => appStore.systemStatus)

const menuRoutes = computed(() => {
  return [
    { path: '/dashboard', meta: { title: '仪表板', icon: 'Dashboard' } },
    { path: '/single-classification', meta: { title: '单条分类', icon: 'Document' } },
    { path: '/batch-processing', meta: { title: '批量处理', icon: 'FolderOpened' } },
    { path: '/template-management', meta: { title: '模板管理', icon: 'Grid' } },
    { path: '/llm-config', meta: { title: 'LLM配置', icon: 'Setting' } }
  ]
})

function toggleCollapse() {
  isCollapse.value = !isCollapse.value
}

async function handleRefresh() {
  try {
    await appStore.fetchSystemStatus()
    ElMessage.success('状态刷新成功')
  } catch (error) {
    ElMessage.error('状态刷新失败')
  }
}

onMounted(() => {
  appStore.initApp()
})
</script>

<style scoped>
.layout-container {
  height: 100vh;
}

.layout-header {
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

.header-left {
  display: flex;
  align-items: center;
}

.logo {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 20px;
  font-weight: 600;
  color: #1f2937;
}

.logo-image {
  height: 36px;
  width: auto;
  object-fit: contain;
}

.logo-text {
  background: linear-gradient(135deg, #409EFF, #67C23A);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.layout-aside {
  background: #fff;
  border-right: 1px solid #e8e8e8;
  transition: width 0.3s;
}

.aside-header {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid #e8e8e8;
}

.collapse-btn {
  width: 40px;
  height: 40px;
  color: #606266;
}

.layout-menu {
  border-right: none;
  height: calc(100vh - 124px);
}

.layout-main {
  background: #f5f5f5;
  padding: 0;
  overflow: hidden;
}

.main-content {
  height: 100%;
  overflow: auto;
  padding: 24px;
}

:deep(.el-menu-item) {
  height: 48px;
  line-height: 48px;
}

:deep(.el-menu-item.is-active) {
  background-color: #e6f7ff;
  border-right: 3px solid #409EFF;
}

/* 移动端适配 */
@media (max-width: 768px) {
  .layout-header {
    padding: 8px 12px;
    height: 50px;
  }
  
  .header-left .logo {
    gap: 6px;
  }
  
  .logo-text {
    font-size: 14px;
    display: none; /* 手机端隐藏文字，只显示logo */
  }
  
  .logo-image {
    height: 32px;
  }
  
  .header-right {
    gap: 6px;
  }
  
  .header-right .el-button {
    padding: 4px 8px;
    font-size: 12px;
  }
  
  .header-right .el-dropdown .el-button {
    padding: 4px 6px;
  }
  
  .layout-container {
    flex-direction: column;
  }
  
  .layout-aside {
    width: 100% !important;
    height: auto;
    order: 2;
  }
  
  .aside-header {
    display: none;
  }
  
  .layout-menu {
    height: 50px;
    display: flex;
    overflow-x: auto;
    flex-direction: row;
    white-space: nowrap;
  }
  
  .layout-menu .el-menu-item {
    min-width: 80px;
    height: 50px;
    line-height: 50px;
    justify-content: center;
    font-size: 12px;
    flex-shrink: 0;
  }
  
  .layout-main {
    order: 3;
    flex: 1;
  }
  
  .main-content {
    padding: 12px;
    height: calc(100vh - 100px);
  }
}

/* 超小屏幕适配 */
@media (max-width: 480px) {
  .layout-header {
    padding: 6px 8px;
    height: 45px;
  }
  
  .logo-image {
    height: 28px;
  }
  
  .header-right .el-button span {
    display: none; /* 隐藏按钮文字，只显示图标 */
  }
  
  .layout-menu .el-menu-item {
    min-width: 60px;
    font-size: 11px;
  }
  
  .layout-menu .el-menu-item span {
    display: none; /* 只显示图标 */
  }
  
  .main-content {
    padding: 8px;
    height: calc(100vh - 95px);
  }
}
</style>