<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { authClient } from '@/api/client'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const traces = ref<any[]>([])
const metrics = ref<any>({})
const loading = ref(true)

onMounted(async () => {
  try {
    const [tracesRes, metricsRes] = await Promise.allSettled([
      authClient.get('/traces'),
      authClient.get('/metrics'),
    ])
    if (tracesRes.status === 'fulfilled') {
      traces.value = tracesRes.value.data.traces || []
    }
    if (metricsRes.status === 'fulfilled') {
      metrics.value = metricsRes.value.data
    }
  } finally {
    loading.value = false
  }
})

function formatLatency(ms: number) {
  if (!ms) return '-'
  return ms < 1000 ? ms.toFixed(0) + 'ms' : (ms / 1000).toFixed(2) + 's'
}
</script>

<template>
  <div class="query-logs">
    <div class="page-header">
      <h1>{{ t('menu.queryLogs') }}</h1>
    </div>

    <!-- 指标统计 -->
    <div class="stats-grid" v-if="metrics.counters">
      <div class="stat-card">
        <div class="stat-label">总查询数</div>
        <div class="stat-value">{{ metrics.counters.total_queries || 0 }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">总错误数</div>
        <div class="stat-value">{{ metrics.counters.total_errors || 0 }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">平均延迟</div>
        <div class="stat-value">{{ formatLatency(metrics.histograms?.query_latency_ms?.avg) }}</div>
      </div>
    </div>

    <!-- 日志表格 -->
    <div class="card">
      <el-table :data="traces" v-loading="loading" style="width: 100%">
        <el-table-column prop="created_at" :label="'时间'" width="180" />
        <el-table-column prop="user_id" :label="'用户'" width="120" />
        <el-table-column prop="query" :label="'查询内容'" min-width="300" show-overflow-tooltip />
        <el-table-column prop="total_ms" :label="'耗时'" width="100">
          <template #default="{ row }">
            {{ formatLatency(row.total_ms) }}
          </template>
        </el-table-column>
        <el-table-column prop="status" :label="'状态'" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.query-logs {
  max-width: 1400px;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h1 {
  font-size: 24px;
  font-weight: 800;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 16px;
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

.card {
  padding: 20px;
  border-radius: var(--radius);
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
</style>
