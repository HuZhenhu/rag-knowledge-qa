<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { authClient } from '@/api/client'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const evaluations = ref<any[]>([])
const loading = ref(true)
const running = ref(false)

onMounted(async () => {
  await fetchEvaluations()
})

async function fetchEvaluations() {
  loading.value = true
  try {
    const { data } = await authClient.get('/evaluations')
    evaluations.value = Array.isArray(data) ? data : (data.evaluations || [])
  } catch {} finally {
    loading.value = false
  }
}

async function runEvaluation() {
  running.value = true
  try {
    await authClient.post('/evaluations/run')
    ElMessage.success('评测已触发')
    await fetchEvaluations()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '评测失败')
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div class="evaluations">
    <div class="page-header">
      <h1>{{ t('menu.evaluations') }}</h1>
      <el-button type="primary" :loading="running" @click="runEvaluation">
        <el-icon><VideoPlay /></el-icon>
        运行评测
      </el-button>
    </div>

    <div class="card">
      <el-table :data="evaluations" v-loading="loading" style="width: 100%">
        <el-table-column prop="version" :label="'版本'" width="150" />
        <el-table-column prop="answer_accuracy" :label="'准确率'" width="100">
          <template #default="{ row }">
            {{ (row.answer_accuracy * 100).toFixed(1) }}%
          </template>
        </el-table-column>
        <el-table-column prop="retrieval_hit_rate" :label="'检索命中率'" width="120">
          <template #default="{ row }">
            {{ (row.retrieval_hit_rate * 100).toFixed(1) }}%
          </template>
        </el-table-column>
        <el-table-column prop="total_cases" :label="'总用例'" width="80" />
        <el-table-column prop="success_cases" :label="'通过'" width="80" />
        <el-table-column prop="created_at" :label="'时间'" width="180" />
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.evaluations {
  max-width: 1200px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
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
}
</style>
