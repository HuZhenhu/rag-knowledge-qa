<script setup lang="ts">
import { ref, nextTick, watch, onMounted } from 'vue'
import { useChat } from '../composables/useChat'
import { useWebSocket } from '../composables/useWebSocket'
import { useDataMonitor } from '../composables/useDataMonitor'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'
import type { Message } from '../types'

const { messages, isLoading, error, sessionId, sendMessage, addAssistantMessage, handleToken, setMessageDone, setError, clearMessages } = useChat()

// 对话历史管理
interface ChatSession {
  id: string
  title: string
  messages: Message[]
  createdAt: number
}
const chatHistory = ref<ChatSession[]>([])
const currentSessionIndex = ref<number>(-1)

function loadHistory() {
  try {
    const saved = localStorage.getItem('chat_history')
    if (saved) chatHistory.value = JSON.parse(saved)
  } catch {}
}
function saveHistory() {
  if (messages.value.length === 0) return
  const title = messages.value.find(m => m.role === 'user')?.content.slice(0, 30) || '新对话'
  if (currentSessionIndex.value >= 0) {
    chatHistory.value[currentSessionIndex.value].messages = [...messages.value]
    chatHistory.value[currentSessionIndex.value].title = title
  } else {
    const session: ChatSession = {
      id: sessionId.value,
      title,
      messages: [...messages.value],
      createdAt: Date.now(),
    }
    chatHistory.value.unshift(session)
    currentSessionIndex.value = 0
  }
  if (chatHistory.value.length > 50) chatHistory.value = chatHistory.value.slice(0, 50)
  localStorage.setItem('chat_history', JSON.stringify(chatHistory.value))
}

function loadSession(index: number) {
  saveHistory()
  const session = chatHistory.value[index]
  messages.value = [...session.messages]
  currentSessionIndex.value = index
  sessionId.value = session.id
  ws.disconnect()
  ws.connect(session.id)
}

function newChat() {
  saveHistory()
  clearMessages()
  currentSessionIndex.value = -1
  ws.disconnect()
  ws.connect(sessionId.value)
}

function deleteSession(index: number) {
  chatHistory.value.splice(index, 1)
  localStorage.setItem('chat_history', JSON.stringify(chatHistory.value))
  if (currentSessionIndex.value === index) newChat()
  else if (currentSessionIndex.value > index) currentSessionIndex.value--
}

onMounted(() => {
  loadHistory()
})

const WS_URL = ref('ws://localhost:8080/ws')
const ws = useWebSocket(WS_URL.value)
ws.connect(sessionId.value)

// 数据监控 WebSocket
const DATA_WS_URL = ref('ws://localhost:8080/ws/data-monitor')
const monitor = useDataMonitor(DATA_WS_URL.value)
monitor.connect()

const dataMonitorMessage = ref('')
monitor.onIndexComplete = (stats) => {
  const parts: string[] = []
  if (stats.added > 0) parts.push(`新增 ${stats.added} 个文件`)
  if (stats.updated > 0) parts.push(`更新 ${stats.updated} 个文件`)
  if (stats.deleted > 0) parts.push(`删除 ${stats.deleted} 个文件`)
  if (stats.errors > 0) parts.push(`失败 ${stats.errors} 个`)
  dataMonitorMessage.value = parts.length ? `索引完成: ${parts.join(', ')}` : '索引完成: 无变化'
  setTimeout(() => { dataMonitorMessage.value = '' }, 5000)
}
monitor.onIndexError = (filename, _errorMsg) => {
  dataMonitorMessage.value = `索引失败: ${filename}`
  setTimeout(() => { dataMonitorMessage.value = '' }, 5000)
}

const pendingSources = ref<Record<string, any[]>>({})

ws.onToken = (messageId: string, token: string) => {
  handleToken(messageId, token)
}

ws.onSources = (messageId: string, sources: any[]) => {
  pendingSources.value[messageId] = sources
}

ws.onDone = (messageId: string, timing: any) => {
  const sources = pendingSources.value[messageId]
  setMessageDone(messageId, sources, timing)
  delete pendingSources.value[messageId]
}

ws.onError = (msg: string) => {
  setError(msg)
}

const chatContainer = ref<HTMLElement>()

function scrollToBottom() {
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

watch(messages, scrollToBottom, { deep: true })

function handleSend(content: string) {
  sendMessage(content)
  const assistantMsg = addAssistantMessage()
  ws.sendQuery(content, assistantMsg.id)
}

// 发送后自动保存历史（监听messages变化）
watch(messages, () => {
  if (!isLoading.value && messages.value.length > 0) {
    saveHistory()
  }
}, { deep: true })

const historyCollapsed = ref(false)
</script>

<template>
  <div class="chat-layout">
    <!-- 侧边栏：对话历史 -->
    <aside class="chat-sidebar" :class="{ collapsed: historyCollapsed }">
      <div class="sidebar-header">
        <span v-if="!historyCollapsed">对话历史</span>
        <el-button text size="small" @click="historyCollapsed = !historyCollapsed">
          <el-icon><Fold v-if="!historyCollapsed" /><Expand v-else /></el-icon>
        </el-button>
      </div>
      <div v-if="!historyCollapsed" class="sidebar-content">
        <el-button type="primary" class="new-chat-btn" @click="newChat">
          <el-icon><Plus /></el-icon>
          开启新对话
        </el-button>
        <div class="session-list">
          <div
            v-for="(session, index) in chatHistory"
            :key="session.id"
            class="session-item"
            :class="{ active: currentSessionIndex === index }"
            @click="loadSession(index)"
          >
            <span class="session-title">{{ session.title }}</span>
            <el-button
              text
              size="small"
              class="delete-btn"
              @click.stop="deleteSession(index)"
            >
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
          <div v-if="chatHistory.length === 0" class="empty-history">
            暂无对话记录
          </div>
        </div>
      </div>
    </aside>

    <!-- 主聊天区域 -->
    <div class="chat-view">
      <div v-if="dataMonitorMessage" class="monitor-toast">
        {{ dataMonitorMessage }}
      </div>

      <div v-if="monitor.indexProgress.value" class="monitor-progress">
        索引进度: {{ monitor.indexProgress.value.current }}/{{ monitor.indexProgress.value.total }}
        {{ monitor.indexProgress.value.filename ? `— ${monitor.indexProgress.value.filename}` : '' }}
      </div>

      <main class="chat-container" ref="chatContainer">
        <div v-if="messages.length === 0" class="empty-state">
          <div class="icon">🤖</div>
          <p>有什么问题？问我吧！</p>
          <div class="status-bar">
            <div class="status" :class="{ connected: monitor.isConnected.value }">
              数据监控: {{ monitor.isConnected.value ? '已连接' : '未连接' }}
            </div>
            <div class="status" :class="{ connected: ws.isConnected.value }">
              对话服务: {{ ws.isConnected.value ? '已连接' : '未连接' }}
            </div>
          </div>
        </div>

        <ChatMessage
          v-for="msg in messages"
          :key="msg.id"
          :message="msg"
        />

        <div v-if="error" class="error-message">
          {{ error }}
        </div>
      </main>

      <ChatInput :loading="isLoading" @send="handleSend" />
    </div>
  </div>
</template>

<style scoped>
.chat-layout {
  display: flex;
  height: 100%;
  margin: 0;
  background: var(--bg-surface);
  border-radius: 8px;
  overflow: hidden;
}

/* 侧边栏 */
.chat-sidebar {
  width: 240px;
  min-width: 240px;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  transition: width 0.2s, min-width 0.2s;
}
.chat-sidebar.collapsed {
  width: 48px;
  min-width: 48px;
}
.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
  font-weight: 600;
}
.sidebar-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 8px;
}
.new-chat-btn {
  width: 100%;
  margin-bottom: 8px;
}
.session-list {
  flex: 1;
  overflow-y: auto;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  margin-bottom: 2px;
  transition: background 0.15s;
}
.session-item:hover {
  background: var(--bg-hover);
}
.session-item.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.session-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.delete-btn {
  opacity: 0;
  transition: opacity 0.15s;
}
.session-item:hover .delete-btn {
  opacity: 1;
}
.empty-history {
  text-align: center;
  color: var(--text-3);
  font-size: 13px;
  padding: 24px 0;
}

/* 主聊天区域 */
.chat-view {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.monitor-toast {
  padding: 8px 16px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 13px;
  text-align: center;
  animation: fadeIn 0.3s ease;
}

.monitor-progress {
  padding: 6px 16px;
  background: var(--warning-bg);
  color: var(--warning);
  font-size: 12px;
  text-align: center;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

.chat-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-2);
}

.empty-state .icon {
  font-size: 64px;
  margin-bottom: 16px;
}

.status-bar {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}

.status {
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 12px;
  background: var(--error-bg);
  color: var(--error);
}

.status.connected {
  background: var(--success-bg);
  color: var(--success);
}

.error-message {
  padding: 12px 16px;
  margin: 8px 0;
  background: var(--error-bg);
  color: var(--error);
  border-radius: 8px;
}
</style>
