<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { authClient } from '@/api/client'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const users = ref<any[]>([])
const loading = ref(true)

onMounted(async () => {
  try {
    const { data } = await authClient.get('/users')
    users.value = data.users || []
  } catch {} finally {
    loading.value = false
  }
})

function getRoleType(role: string) {
  const map: Record<string, string> = {
    admin: 'danger',
    editor: 'warning',
    viewer: 'info',
  }
  return map[role] || 'info'
}
</script>

<template>
  <div class="users-page">
    <div class="page-header">
      <h1>{{ t('menu.users') }}</h1>
    </div>

    <div class="card">
      <el-table :data="users" v-loading="loading" style="width: 100%">
        <el-table-column prop="username" :label="'用户名'" width="150" />
        <el-table-column prop="role" :label="'角色'" width="120">
          <template #default="{ row }">
            <el-tag :type="getRoleType(row.role)" size="small">{{ row.role }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_active" :label="'状态'" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_login" :label="'最后登录'" width="180" />
        <el-table-column prop="created_at" :label="'创建时间'" width="180" />
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.users-page {
  max-width: 1200px;
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
}
</style>
