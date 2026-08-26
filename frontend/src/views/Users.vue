<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { authClient } from '@/api/client'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const authStore = useAuthStore()

const users = ref<any[]>([])
const loading = ref(true)
const saving = ref<string | null>(null)

const ROLE_OPTIONS = ['viewer', 'editor', 'admin']

onMounted(fetchUsers)

async function fetchUsers() {
  loading.value = true
  try {
    const { data } = await authClient.get('/users')
    users.value = data.users || []
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('common.error'))
  } finally {
    loading.value = false
  }
}

function getRoleType(role: string) {
  const map: Record<string, string> = {
    admin: 'danger',
    editor: 'warning',
    viewer: 'info',
  }
  return map[role] || 'info'
}

function getRoleLabel(role: string): string {
  return (t(`common.roles.${role}`) as string) || role
}

function isSelf(row: any): boolean {
  return !!authStore.user && authStore.user.id === row.id
}

/** A4：修改角色即调用 PUT /api/v1/users/{id}/role */
async function handleRoleChange(row: any, newRole: string) {
  if (isSelf(row)) {
    ElMessage.warning(t('users.cannotModifySelfRole'))
    await fetchUsers() // 回滚下拉
    return
  }
  saving.value = row.id
  try {
    await authClient.put(`/users/${row.id}/role`, { role: newRole })
    ElMessage.success(t('users.roleUpdated'))
    await fetchUsers()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('users.roleUpdateFailed'))
    await fetchUsers()
  } finally {
    saving.value = null
  }
}
</script>

<template>
  <div class="users-page">
    <div class="page-header">
      <h1>{{ t('menu.users') }}</h1>
      <el-button :loading="loading" @click="fetchUsers">
        <el-icon><Refresh /></el-icon>
        {{ t('common.refresh') }}
      </el-button>
    </div>

    <div class="card">
      <el-table :data="users" v-loading="loading" style="width: 100%">
        <el-table-column prop="username" label="用户名" min-width="200">
          <template #default="{ row }">
            <span class="username">
              <span>{{ row.username }}</span>
              <el-tag v-if="isSelf(row)" size="small" type="info" effect="plain">
                {{ t('users.self') }}
              </el-tag>
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="role" :label="t('users.role')" width="200">
          <template #default="{ row }">
            <div class="role-cell" :class="{ 'is-self': isSelf(row) }">
              <el-tag :type="getRoleType(row.role)" size="small">{{ getRoleLabel(row.role) }}</el-tag>
              <el-tooltip
                :content="isSelf(row) ? t('users.cannotModifySelfRole') : ''"
                placement="top"
                :disabled="!isSelf(row)"
              >
                <el-select
                  :model-value="row.role"
                  class="role-select"
                  size="small"
                  :disabled="isSelf(row)"
                  :loading="saving === row.id"
                  @change="(v: string) => handleRoleChange(row, v)"
                >
                  <el-option v-for="r in ROLE_OPTIONS" :key="r" :label="getRoleLabel(r)" :value="r" />
                </el-select>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_login" label="最后登录" width="180" />
        <el-table-column label="ID" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">
            <code class="user-id">{{ row.id }}</code>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.users-page {
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

.username {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.role-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.role-cell.is-self {
  opacity: 0.85;
}

.role-select {
  width: 120px;
}

.user-id {
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 12px;
  color: var(--text-2);
}
</style>
