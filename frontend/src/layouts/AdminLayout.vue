<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useI18n } from 'vue-i18n'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const themeStore = useThemeStore()
const { t, locale } = useI18n()

const isCollapse = ref(false)

const menuItems = computed(() => [
  { path: '/dashboard', icon: 'Grid', label: t('menu.dashboard') },
  { path: '/documents', icon: 'Document', label: t('menu.documents') },
  { path: '/index', icon: 'Connection', label: t('menu.indexMonitor') },
  { path: '/logs', icon: 'TrendCharts', label: t('menu.queryLogs') },
  { path: '/evaluations', icon: 'DataAnalysis', label: t('menu.evaluations') },
  { path: '/users', icon: 'User', label: t('menu.users') },
  { path: '/chat', icon: 'ChatDotRound', label: t('menu.chat') },
])

const activeMenu = computed(() => {
  const path = route.path
  return '/' + path.split('/')[1]
})

function toggleLocale() {
  locale.value = locale.value === 'zh' ? 'en' : 'zh'
  localStorage.setItem('locale', locale.value)
}

function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<template>
  <div class="admin-layout">
    <!-- 侧栏 -->
    <aside class="sidebar" :class="{ collapsed: isCollapse }">
      <div class="logo">
        <div class="logo-icon">R</div>
        <span v-if="!isCollapse" class="logo-text">RAG Admin</span>
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapse"
        router
        class="sidebar-menu"
      >
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>{{ item.label }}</template>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-footer">
        <div class="user-info">
          <el-avatar :size="28">{{ authStore.user?.username?.[0]?.toUpperCase() }}</el-avatar>
          <div v-if="!isCollapse">
            <div class="user-name">{{ authStore.user?.username }}</div>
            <div class="user-role">{{ authStore.user?.role }}</div>
          </div>
        </div>
      </div>
    </aside>

    <!-- 主内容区 -->
    <div class="main-wrapper">
      <!-- 顶栏 -->
      <header class="header">
        <div class="header-left">
          <el-button :icon="isCollapse ? 'Expand' : 'Fold'" text @click="isCollapse = !isCollapse" />
        </div>
        <div class="header-right">
          <el-button text @click="toggleLocale">
            {{ locale === 'zh' ? 'EN' : '中' }}
          </el-button>
          <el-button :icon="themeStore.theme === 'dark' ? 'Sunny' : 'Moon'" text @click="themeStore.toggleTheme()" />
          <el-dropdown @command="handleLogout">
            <span class="user-dropdown">
              {{ authStore.user?.username }}
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item>{{ t('common.logout') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <!-- 内容区 -->
      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.admin-layout {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  width: 240px;
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  transition: width 0.3s;
}

.sidebar.collapsed {
  width: 64px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 16px;
}

.logo-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--accent), #6D28D9);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 800;
  color: white;
  flex-shrink: 0;
}

.logo-text {
  font-size: 14px;
  font-weight: 700;
}

.sidebar-menu {
  flex: 1;
  border-right: none;
}

.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-name {
  font-size: 12px;
  font-weight: 600;
}

.user-role {
  font-size: 10px;
  color: var(--text-3);
}

.main-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.header {
  height: 56px;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
}

.header-left {
  display: flex;
  align-items: center;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-dropdown {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  font-size: 13px;
}

.content {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
}
</style>
