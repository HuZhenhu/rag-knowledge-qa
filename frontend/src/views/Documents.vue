<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { authClient } from '@/api/client'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const authStore = useAuthStore()

const documents = ref<any[]>([])
const loading = ref(true)
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

// Chunk Drawer
const drawerVisible = ref(false)
const currentDoc = ref<any>(null)
const chunks = ref<any[]>([])
const chunksLoading = ref(false)

onMounted(async () => {
  await fetchDocuments()
})

async function fetchDocuments() {
  loading.value = true
  try {
    const { data } = await authClient.get('/documents')
    documents.value = data.documents || []
  } catch (e: any) {
    ElMessage.error('获取文档列表失败')
  } finally {
    loading.value = false
  }
}

/** A3：上传文档 = editor+（仅对 canEdit 显示入口） */
function triggerUpload() {
  fileInput.value?.click()
}

async function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true
  const formData = new FormData()
  formData.append('file', file)
  try {
    await authClient.post('/documents/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    ElMessage.success(`「${file.name}」上传成功`)
    await fetchDocuments()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function viewChunks(doc: any) {
  currentDoc.value = doc
  drawerVisible.value = true
  chunksLoading.value = true
  try {
    const { data } = await authClient.get(`/documents/${doc.id}/chunks`)
    chunks.value = data.chunks || []
  } catch (e: any) {
    ElMessage.error('获取 Chunks 失败')
  } finally {
    chunksLoading.value = false
  }
}

async function copyChunk(content: string) {
  try {
    await navigator.clipboard.writeText(content)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

async function copyAllChunks() {
  const text = chunks.value
    .map((c, i) => `### Chunk ${i + 1}

${c.content}`)
    .join(`

---

`)
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(`已复制全部 ${chunks.value.length} 个 chunks`)
  } catch {
    ElMessage.error('复制失败')
  }
}

async function deleteDocument(doc: any) {
  try {
    await ElMessageBox.confirm(`确定删除文档「${doc.filename}」？此操作不可恢复。`, '删除确认', {
      type: 'warning',
    })
    await authClient.delete(`/documents/${doc.id}`)
    ElMessage.success('删除成功')
    await fetchDocuments()
  } catch {}
}

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
  <div class="documents-page">
    <div class="page-header">
      <h1>{{ t('menu.documents') }}</h1>
      <div class="header-actions">
        <el-button
          v-if="authStore.canEdit"
          type="primary"
          :loading="uploading"
          @click="triggerUpload"
        >
          <el-icon><Upload /></el-icon>
          {{ t('documents.upload') }}
        </el-button>
        <el-button @click="fetchDocuments">
          <el-icon><Refresh /></el-icon>
          {{ t('common.refresh') }}
        </el-button>
      </div>
      <input
        ref="fileInput"
        type="file"
        hidden
        accept=".txt,.md,.pdf,.doc,.docx"
        @change="handleFileChange"
      />
    </div>

    <div class="card">
      <el-table :data="documents" v-loading="loading" style="width: 100%">
        <el-table-column prop="filename" :label="'文件名'" min-width="200">
          <template #default="{ row }">
            <span style="font-weight: 600">{{ row.filename }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="file_type" :label="'类型'" width="80">
          <template #default="{ row }">
            <el-tag size="small">{{ row.file_type?.toUpperCase() }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="chunks" :label="'Chunks'" width="80" />
        <el-table-column prop="indexed_at" :label="'索引时间'" width="180" />
        <el-table-column prop="status" :label="'状态'" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="'操作'" width="180" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="viewChunks(row)">查看 Chunks</el-button>
            <el-button
              v-if="authStore.canEdit"
              size="small"
              type="danger"
              @click="deleteDocument(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Chunks Drawer -->
    <el-drawer
      v-model="drawerVisible"
      :title="currentDoc?.filename"
      size="600px"
    >
      <div class="drawer-header">
        <span>共 {{ chunks.length }} 个 chunks</span>
        <el-button size="small" @click="copyAllChunks">
          <el-icon><DocumentCopy /></el-icon>
          复制全部
        </el-button>
      </div>
      <div v-loading="chunksLoading" class="chunks-list">
        <div v-for="(chunk, index) in chunks" :key="chunk.chunk_id" class="chunk-card">
          <div class="chunk-header">
            <span class="chunk-index">#{{ index + 1 }}</span>
            <el-tag size="small" v-if="chunk.page_number">第{{ chunk.page_number }}页</el-tag>
            <el-tag size="small" type="info" v-if="chunk.content_type">{{ chunk.content_type }}</el-tag>
            <el-button size="small" text @click="copyChunk(chunk.content)">
              <el-icon><DocumentCopy /></el-icon>
              复制
            </el-button>
          </div>
          <pre class="chunk-content">{{ chunk.content }}</pre>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.documents-page {
  max-width: 1400px;
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

.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}

.chunks-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chunk-card {
  padding: 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  border: 1px solid var(--border);
}

.chunk-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.chunk-index {
  font-weight: 700;
  font-size: 13px;
}

.chunk-content {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
}
</style>
