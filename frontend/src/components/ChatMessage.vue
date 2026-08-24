<script setup lang="ts">
import { ref } from 'vue'
import type { Message } from '../types'
import AgentTraceTimeline from './AgentTraceTimeline.vue'

const props = defineProps<{
  message: Message
}>()

const showSources = ref(false)

function toggleSources(): void {
  if (props.message.sources && props.message.sources.length > 0) {
    showSources.value = !showSources.value
  }
}

function formatContent(content: string): string {
  return content.replace(
    /\[(\d+)\]/g,
    '<span class="citation" data-index="$1">[$1]</span>'
  )
}
</script>

<template>
  <div
    class="message"
    :class="message.role === 'user' ? 'message-user' : 'message-assistant'"
  >
    <div class="message-avatar">
      {{ message.role === 'user' ? '👤' : '🤖' }}
    </div>

    <div class="message-content" @click="toggleSources">
      <div v-if="message.role === 'assistant' && !message.content" class="loading">
        <span></span><span></span><span></span>
      </div>

      <div v-else class="message-text" v-html="formatContent(message.content)"></div>

      <div v-if="showSources && message.sources" class="sources-panel">
        <div class="sources-header">
          参考来源
          <span v-if="message.timing" class="timing">
            {{ message.timing.total_ms }}ms
          </span>
        </div>
        <div v-for="(source, idx) in message.sources" :key="idx" class="source-item">
          <div class="source-file">{{ source.file }} - {{ source.section }}</div>
          <div class="source-chunk">{{ source.chunk }}</div>
          <div class="source-score">相关度: {{ (source.score * 100).toFixed(0) }}%</div>
        </div>
      </div>

      <!-- Agent 推理过程时间线（Agentic RAG 前端可视化，M5）
           仅 agentic 引擎消息带 agent_trace；普通引擎消息无该字段，不渲染 -->
      <AgentTraceTimeline
        v-if="message.role === 'assistant' && message.agent_trace && message.agent_trace.length > 0"
        :trace="message.agent_trace"
      />
    </div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  padding: 8px;
}

.message-user {
  flex-direction: row-reverse;
}

.message-user .message-content {
  background: var(--el-color-primary);
  color: white;
  border-radius: 16px 16px 4px 16px;
}

.message-assistant .message-content {
  background: var(--el-fill-color-light);
  border-radius: 16px 16px 16px 4px;
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}

.message-content {
  max-width: 70%;
  padding: 12px 16px;
}

.loading {
  display: flex;
  gap: 4px;
  padding: 8px 0;
}

.loading span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-text-color-secondary);
  animation: bounce 1.4s infinite ease-in-out both;
}

.loading span:nth-child(1) { animation-delay: -0.32s; }
.loading span:nth-child(2) { animation-delay: -0.16s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.sources-panel {
  margin-top: 12px;
  padding: 12px;
  background: var(--el-bg-color);
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
}

.sources-header {
  font-weight: 600;
  margin-bottom: 8px;
  display: flex;
  justify-content: space-between;
}

.timing {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.source-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.source-item:last-child {
  border-bottom: none;
}

.source-file {
  font-size: 12px;
  color: var(--el-color-primary);
  font-weight: 500;
}

.source-chunk {
  font-size: 13px;
  margin: 4px 0;
}

.source-score {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.citation {
  color: var(--el-color-primary);
  cursor: pointer;
  font-weight: 600;
}
</style>
