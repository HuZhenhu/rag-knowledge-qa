<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { authClient } from '@/api/client'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const stats = ref({
  documents: 0,
  chunks: 0,
  queriesToday: 0,
  avgLatency: '0s',
})

const recentDocs = ref<any[]>([])
const alerts = ref<any[]>([])
const loading = ref(true)

onMounted(async () => {
  try {
    const [healthRes, metricsRes] = await Promise.allSettled([
      authClient.get('/health'),
      authClient.get('/metrics'),
    ])

    if (healthRes.status === 'fulfilled') {
      const data = healthRes.value.data
      stats.value.chunks = data.vector_store?.count || 0
    }

    if (metricsRes.status === 'fulfilled') {
      const data = metricsRes.value.data
      stats.value.queriesToday = data.counters?.total_queries || 0
      const latency = data.histograms?.query_latency_ms?.avg
      stats.value.avgLatency = latency ? (latency / 1000).toFixed(1) + 's' : '0s'
    }

    try {
      const docsRes = await authClient.get('/documents')
      const docs = docsRes.data.documents || []
      recentDocs.value = docs.slice(0, 5)
      stats.value.documents = docs.length
    } catch {}

    try {
      const alertsRes = await authClient.get('/alerts')
      alerts.value = (alertsRes.data.alerts || []).slice(0, 5)
    } catch {}
  } finally {
    loading.value = false
  }
})

function getStatusType(status: string) {
  const map: Record<string, string> = {
    indexed: 'success',
    pending: 'warning',
    error: 'danger',
  }
  return map[status] || 'info'
}
</script>

<template>
  <div class="dashboard">
    <div class="page-header">
      <div>
        <h1>{{ t('dashboard.title') }}</h1>
        <p class="subtitle">{{ t('dashboard.subtitle') }}</p>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">{{ t('dashboard.documents') }}</div>
        <div class="stat-value">{{ stats.documents }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">{{ t('dashboard.chunks') }}</div>
        <div class="stat-value">{{ stats.chunks.toLocaleString() }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">{{ t('dashboard.queriesToday') }}</div>
        <div class="stat-value">{{ stats.queriesToday }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">{{ t('dashboard.avgLatency') }}</div>
        <div class="stat-value">{{ stats.avgLatency }}</div>
      </div>
    </div>

    <!-- 内容区 -->
    <div class="content-grid">
      <!-- 最近文档 -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">{{ t('dashboard.recentDocs') }}</span>
          <el-tag type="info" size="small">{{ recentDocs.length }} {{ t('common.total') }}</el-tag>
        </div>
        <el-table :data="recentDocs" style="width: 100%">
          <el-table-column prop="filename" :label="'文件名'" />
          <el-table-column prop="chunks" :label="'Chunks'" width="80" />
          <el-table-column prop="indexed_at" :label="'索引时间'" width="160" />
        </el-table>
      </div>

      <!-- 系统告警 -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">{{ t('dashboard.systemAlerts') }}</span>
          <el-tag type="success" size="small">Healthy</el-tag>
        </div>
        <div class="alerts-list">
          <div v-for="alert in alerts" :key="alert.id" class="alert-item">
            <div class="alert-dot" :class="alert.level"></div>
            <div>
              <div class="alert-text">{{ alert.message }}</div>
              <div class="alert-time">{{ alert.created_at }}</div>
            </div>
          </div>
          <div v-if="alerts.length === 0" class="no-data">暂无告警</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  max-width: 1400px;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h1 {
  font-size: 24px;
  font-weight: 800;
}

.subtitle {
  font-size: 13px;
  color: var(--text-3);
  margin-top: 4px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 24px;
}

.stat-card {
  padding: 20px;
  border-radius: var(--radius);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  transition: transform 0.2s;
}

.stat-card:hover {
  transform: translateY(-2px);
}

.stat-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 8px;
}

.stat-value {
  font-size: 28px;
  font-weight: 800;
}

.content-grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 12px;
}

.card {
  padding: 20px;
  border-radius: var(--radius);
  background: var(--bg-surface);
  border: 1px solid var(--border);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.card-title {
  font-size: 14px;
  font-weight: 700;
}

.alerts-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.alert-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}

.alert-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.alert-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-top: 6px;
  flex-shrink: 0;
}

.alert-dot.green, .alert-dot.success { background: var(--success); }
.alert-dot.yellow, .alert-dot.warning { background: var(--warning); }
.alert-dot.red, .alert-dot.error { background: var(--error); }

.alert-text {
  font-size: 12px;
  color: var(--text-2);
}

.alert-time {
  font-size: 10px;
  color: var(--text-3);
  margin-top: 2px;
}

.no-data {
  text-align: center;
  color: var(--text-3);
  font-size: 13px;
  padding: 20px;
}
</style>
