import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'
import i18n from '@/i18n'
import { canAccessRoute } from '@/utils/permission'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/',
    component: () => import('@/layouts/AdminLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/dashboard' },
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        // 所有登录用户（不设 meta.roles = 仅需登录）
      },
      {
        path: 'documents',
        name: 'Documents',
        component: () => import('@/views/Documents.vue'),
        meta: { roles: ['viewer', 'editor', 'admin'] },
      },
      {
        path: 'index',
        name: 'IndexMonitor',
        component: () => import('@/views/IndexMonitor.vue'),
        meta: { roles: ['editor', 'admin'] },
      },
      {
        path: 'logs',
        name: 'QueryLogs',
        component: () => import('@/views/QueryLogs.vue'),
        meta: { roles: ['admin'] },
      },
      {
        path: 'evaluations',
        name: 'Evaluations',
        component: () => import('@/views/Evaluations.vue'),
        meta: { roles: ['admin'] },
      },
      {
        path: 'users',
        name: 'Users',
        component: () => import('@/views/Users.vue'),
        meta: { roles: ['admin'] },
      },
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/views/Chat.vue'),
        // 所有登录用户（不设 meta.roles）
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, _from, next) => {
  const authStore = useAuthStore()

  if (to.meta.requiresAuth !== false && !authStore.isLoggedIn) {
    next('/login')
    return
  }

  // A1：按 meta.roles 收紧路由级权限，无权限访问重定向 /dashboard 并提示
  const allowed = (to.meta.roles as string[] | undefined)
  if (authStore.isLoggedIn && !canAccessRoute(authStore.user?.role, allowed)) {
    ElMessage.warning(i18n.global.t('common.noPermission'))
    next('/dashboard')
    return
  }

  next()
})

export default router
