<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import { useChat } from '../composables/useChat'
import { useWebSocket } from '../composables/useWebSocket'
import { useDataMonitor } from '../composables/useDataMonitor'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'

const { messages, isLoading, error, sessionId, sendMessage, addAssistantMessage, handleToken, setMessageDone, setError } = useChat()

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
</script>

<template>
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
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  margin: 0;
  background: var(--bg-surface);
  border-radius: 8px;
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
