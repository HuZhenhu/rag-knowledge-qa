<script setup lang="ts">
import { ref, nextTick, watch, onMounted } from 'vue'
import { useChat } from '../composables/useChat'
import { useWebSocket } from '../composables/useWebSocket'
import { useDataMonitor } from '../composables/useDataMonitor'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'
import type { Message, AgentTraceEvent } from '../types'

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

// Agent 推理过程事件缓冲（Agentic RAG 前端可视化，M5，设计文档 §4.2）
// 各 agent 事件先按 message_id 收集，done 时一并写入消息的 agent_trace
const pendingAgentTraces = ref<Record<string, AgentTraceEvent[]>>({})

function appendAgentTrace(messageId: string, event: AgentTraceEvent): void {
  if (!pendingAgentTraces.value[messageId]) {
    pendingAgentTraces.value[messageId] = []
  }
  pendingAgentTraces.value[messageId].push(event)
}

ws.onToken = (messageId: string, token: string) => {
  handleToken(messageId, token)
}

ws.onSources = (messageId: string, sources: any[]) => {
  pendingSources.value[messageId] = sources
}

ws.onDone = (messageId: string, timing: any) => {
  const sources = pendingSources.value[messageId]
  const trace = pendingAgentTraces.value[messageId] || []
  setMessageDone(messageId, sources, timing, trace)
  delete pendingSources.value[messageId]
  delete pendingAgentTraces.value[messageId]
}

ws.onError = (msg: string) => {
  setError(msg)
}

// Agent 推理过程事件（仅 agentic 引擎产生；普通引擎不产生，此处回调不触发）
ws.onAgentPlan = (messageId: string, event: AgentTraceEvent) => appendAgentTrace(messageId, event)
ws.onAgentToolCall = (messageId: string, event: AgentTraceEvent) => appendAgentTrace(messageId, event)
ws.onAgentEvidence = (messageId: string, event: AgentTraceEvent) => appendAgentTrace(messageId, event)
ws.onAgentReflect = (messageId: string, event: AgentTraceEvent) => appendAgentTrace(messageId, event)
ws.onAgentFinal = (messageId: string, event: AgentTraceEvent) => appendAgentTrace(messageId, event)

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
        <span v-if="!historyCollapsed" class="sidebar-title">对话历史</span>
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
      <div v-if="dataMonitorMessage" class="monitor-toast mono">
        {{ dataMonitorMessage }}
      </div>

      <div v-if="monitor.indexProgress.value" class="monitor-progress mono">
        索引进度: {{ monitor.indexProgress.value.current }}/{{ monitor.indexProgress.value.total }}
        {{ monitor.indexProgress.value.filename ? `— ${monitor.indexProgress.value.filename}` : '' }}
      </div>

      <main class="chat-container" ref="chatContainer">
        <div v-if="messages.length === 0" class="empty-state">
          <div class="icon mono">R</div>
          <p class="empty-title">有什么问题？问我吧！</p>
          <p class="empty-sub">按领域调用知识库检索与联网搜索，附引用来源与推理时间线</p>
          <div class="status-bar">
            <div class="status mono" :class="{ connected: monitor.isConnected.value }">
              DATA: {{ monitor.isConnected.value ? 'ON' : 'OFF' }}
            </div>
            <div class="status mono" :class="{ connected: ws.isConnected.value }">
              WS: {{ ws.isConnected.value ? 'ON' : 'OFF' }}
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
  overflow: hidden;
  background: var(--bg);
}

/* 侧边栏 */
.chat-sidebar {
  width: 252px;
  min-width: 252px;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  transition: width 0.2s, min-width 0.2s;
}
.chat-sidebar.collapsed {
  width: 52px;
  min-width: 52px;
}
.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.03em;
}
.sidebar-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 10px;
}
.new-chat-btn {
  width: 100%;
  margin-bottom: 10px;
  font-weight: 600;
}
.session-list {
  flex: 1;
  overflow-y: auto;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  margin-bottom: 3px;
  transition: background 0.15s ease;
}
.session-item:hover {
  background: var(--bg-hover);
}
.session-item.active {
  background: var(--accent-soft);
  color: var(--accent);
  box-shadow: inset 3px 0 0 var(--accent);
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
  padding: 28px 0;
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
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  text-align: center;
  animation: fadeIn 0.3s ease;
}

.monitor-progress {
  padding: 6px 16px;
  background: var(--warning-bg);
  color: var(--warning);
  border-bottom: 1px solid var(--border);
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
  padding: 28px 20px 20px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-2);
  text-align: center;
}

.empty-state .icon {
  width: 64px;
  height: 64px;
  border-radius: var(--radius-lg);
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 20px;
  box-shadow: var(--shadow-sm);
}

.empty-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--text-1);
}

.empty-sub {
  margin-top: 8px;
  font-size: 13px;
  color: var(--text-3);
}

.status-bar {
  display: flex;
  gap: 10px;
  margin-top: 28px;
}

.status {
  font-size: 11px;
  padding: 4px 12px;
  border-radius: var(--radius-full);
  background: var(--error-bg);
  color: var(--error);
  letter-spacing: 0.04em;
  border: 1px solid var(--border);
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
  border-radius: var(--radius-sm);
  border: 1px solid var(--error-bg);
}
</style>
