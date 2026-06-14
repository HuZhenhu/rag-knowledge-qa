import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

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
      { path: 'dashboard', name: 'Dashboard', component: () => import('@/views/Dashboard.vue') },
      { path: 'documents', name: 'Documents', component: () => import('@/views/Documents.vue'), meta: { roles: ['viewer', 'editor', 'admin'] } },
      { path: 'index', name: 'IndexMonitor', component: () => import('@/views/IndexMonitor.vue'), meta: { roles: ['viewer', 'editor', 'admin'] } },
      { path: 'logs', name: 'QueryLogs', component: () => import('@/views/QueryLogs.vue'), meta: { roles: ['viewer', 'editor', 'admin'] } },
      { path: 'evaluations', name: 'Evaluations', component: () => import('@/views/Evaluations.vue'), meta: { roles: ['viewer', 'editor', 'admin'] } },
      { path: 'users', name: 'Users', component: () => import('@/views/Users.vue'), meta: { roles: ['viewer', 'editor', 'admin'] } },
      { path: 'chat', name: 'Chat', component: () => import('@/views/Chat.vue'), meta: { roles: ['viewer', 'editor', 'admin'] } },
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
  } else if (to.meta.roles && !(to.meta.roles as string[]).includes(authStore.user?.role || '')) {
    next('/dashboard')
  } else {
    next()
  }
})

export default router
