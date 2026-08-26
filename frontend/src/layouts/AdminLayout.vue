<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useI18n } from 'vue-i18n'
import { filterMenusByRole } from '@/utils/menus'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const themeStore = useThemeStore()
const { t, locale } = useI18n()

const isCollapse = ref(false)

/** A2：菜单按角色过滤（minRole 与路由 meta.roles 同源） */
const menuItems = computed(() =>
  filterMenusByRole(authStore.user?.role).map((item) => ({
    path: item.path,
    icon: item.icon,
    label: t(item.labelKey),
  }))
)

/** 角色可读标签（如 管理员 / 编辑 / 只读） */
const roleLabel = computed(() => {
  const role = authStore.user?.role ?? 'viewer'
  return (t(`common.roles.${role}`) as string) || role
})

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
    <aside class="sidebar bg-grid" :class="{ collapsed: isCollapse }">
      <div class="logo">
        <div class="logo-icon mono">R</div>
        <span v-if="!isCollapse" class="logo-text">Knowledge Works</span>
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
          <el-avatar :size="28" class="user-avatar">{{ authStore.user?.username?.[0]?.toUpperCase() }}</el-avatar>
          <div v-if="!isCollapse">
            <div class="user-name">{{ authStore.user?.username }}</div>
            <div class="user-role">{{ roleLabel }}</div>
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

      <!-- 内容区（全局页面切换过渡） -->
      <main class="content">
        <router-view v-slot="{ Component }">
          <transition name="fade-slide" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
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
  position: sticky;
  top: 0;
  height: 100vh;
}

.sidebar.collapsed {
  width: 64px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 16px;
  border-bottom: 1px solid var(--border);
}

.logo-icon {
  width: 34px;
  height: 34px;
  border-radius: var(--radius-sm);
  background: var(--accent);
  box-shadow: var(--shadow-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 700;
  color: var(--bg-surface);
  flex-shrink: 0;
  letter-spacing: 0.02em;
}

.logo-text {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.sidebar-menu {
  flex: 1;
  border-right: none;
  padding: 10px 8px;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--text-2);
  --el-menu-hover-bg-color: var(--bg-hover);
  --el-menu-active-color: var(--accent);
  --el-menu-item-height: 44px;
}

.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.user-avatar {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 700;
}

.user-name {
  font-size: 12.5px;
  font-weight: 600;
}

.user-role {
  font-size: 10.5px;
  color: var(--text-3);
  margin-top: 1px;
}

.main-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
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
  color: var(--text-2);
}

.content {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
  background: var(--bg);
}
</style>
