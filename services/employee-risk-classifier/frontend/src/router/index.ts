import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Layout',
      component: () => import('@/views/Layout.vue'),
      redirect: '/dashboard',
      children: [
        {
          path: '/dashboard',
          name: 'Dashboard',
          component: () => import('@/views/Dashboard.vue'),
          meta: { title: '仪表板', icon: 'Dashboard' }
        },
        {
          path: '/single-classification',
          name: 'SingleClassification',
          component: () => import('@/views/SingleClassification.vue'),
          meta: { title: '单条分类', icon: 'Document' }
        },
        {
          path: '/batch-processing',
          name: 'BatchProcessing',
          component: () => import('@/views/BatchProcessing.vue'),
          meta: { title: '批量处理', icon: 'FolderOpened' }
        },
        {
          path: '/template-management',
          name: 'TemplateManagement',
          component: () => import('@/views/TemplateManagement.vue'),
          meta: { title: '模板管理', icon: 'Grid' }
        },
        {
          path: '/llm-config',
          name: 'LLMConfig',
          component: () => import('@/views/LLMConfig.vue'),
          meta: { title: 'LLM配置', icon: 'Setting' }
        }
      ]
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('@/views/NotFound.vue')
    }
  ]
})

export default router