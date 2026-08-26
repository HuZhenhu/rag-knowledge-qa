<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { authClient } from '@/api/client'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const authStore = useAuthStore()

const watcherStatus = ref({ running: false })
const indexStatus = ref({ pending: 0, indexed: 0, error: 0 })
const loading = ref(true)

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  await fetchStatus()
  // 每 5 秒轮询状态
  pollTimer = setInterval(fetchStatus, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

async function fetchStatus() {
  try {
    const [watcherRes, indexRes] = await Promise.allSettled([
      authClient.get('/index/watcher/status'),
      authClient.get('/index/status'),
    ])
    if (watcherRes.status === 'fulfilled') {
      watcherStatus.value = watcherRes.value.data
    }
    if (indexRes.status === 'fulfilled') {
      indexStatus.value = indexRes.value.data
    }
  } finally {
    loading.value = false
  }
}

async function toggleWatcher() {
  try {
    const endpoint = watcherStatus.value.running
      ? '/index/watcher/stop'
      : '/index/watcher/start'
    await authClient.post(endpoint)
    ElMessage.success(watcherStatus.value.running ? '已停止监听' : '已启动监听')
    await fetchStatus()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

async function triggerSync() {
  try {
    await authClient.post('/index/sync')
    ElMessage.success('同步已触发')
    await fetchStatus()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '同步失败')
  }
}
</script>

<template>
  <div class="index-monitor">
    <div class="page-header">
      <h1>{{ t('menu.indexMonitor') }}</h1>
    </div>

    <!-- Watcher 状态 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">文件监听器</span>
        <el-tag :type="watcherStatus.running ? 'success' : 'info'" size="small">
          {{ watcherStatus.running ? '运行中' : '已停止' }}
        </el-tag>
      </div>
      <div class="card-body">
        <p class="description">监听 data/ 目录变化，自动触发增量索引</p>
        <div class="actions">
          <el-button v-if="authStore.isAdmin" @click="toggleWatcher">
            <el-icon><component :is="watcherStatus.running ? 'VideoPause' : 'VideoPlay'" /></el-icon>
            {{ watcherStatus.running ? '停止监听' : '启动监听' }}
          </el-button>
          <el-button v-if="authStore.isAdmin" @click="triggerSync">
            <el-icon><Refresh /></el-icon>
            手动同步
          </el-button>
          <span v-if="!authStore.isAdmin" class="readonly-hint">只读：扫描 / 同步操作仅管理员可用</span>
        </div>
      </div>
    </div>

    <!-- 索引统计 -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">待索引</div>
        <div class="stat-value">{{ indexStatus.pending }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">已索引</div>
        <div class="stat-value">{{ indexStatus.indexed }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">错误</div>
        <div class="stat-value" :class="{ 'text-error': indexStatus.error > 0 }">{{ indexStatus.error }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.index-monitor {
  max-width: 800px;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h1 {
  font-size: 24px;
  font-weight: 800;
}

.card {
  padding: 20px;
  border-radius: var(--radius);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.card-title {
  font-size: 14px;
  font-weight: 700;
}

.description {
  font-size: 13px;
  color: var(--text-2);
  margin-bottom: 16px;
}

.actions {
  display: flex;
  gap: 8px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.stat-card {
  padding: 20px;
  border-radius: var(--radius);
  background: var(--bg-surface);
  border: 1px solid var(--border);
}

.stat-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-3);
  text-transform: uppercase;
  margin-bottom: 8px;
}

.stat-value {
  font-size: 28px;
  font-weight: 800;
}

.text-error {
  color: var(--error);
}
</style>
