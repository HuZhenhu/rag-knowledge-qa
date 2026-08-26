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
    <div class="message-avatar mono">
      {{ message.role === 'user' ? 'U' : 'A' }}
    </div>

    <div class="message-content" @click="toggleSources">
      <div v-if="message.role === 'assistant' && !message.content" class="loading">
        <span></span><span></span><span></span>
      </div>

      <div v-else class="message-text" v-html="formatContent(message.content)"></div>

      <!-- 流式输出光标：timing 在 done 时写入，未 done 且有内容即视为流式中 -->
      <span
        v-if="message.role === 'assistant' && message.content && !message.timing"
        class="stream-caret"
      ></span>

      <div v-if="showSources && message.sources" class="sources-panel">
        <div class="sources-header">
          <span class="sources-title">参考来源</span>
          <span v-if="message.sources" class="sources-count mono">{{ message.sources.length }} 条</span>
          <span v-if="message.timing" class="timing mono">{{ message.timing.total_ms }}ms</span>
        </div>
        <div v-for="(source, idx) in message.sources" :key="idx" class="source-item">
          <div class="source-file mono">{{ source.file }} - {{ source.section }}</div>
          <div class="source-chunk">{{ source.chunk }}</div>
          <div class="source-score mono">相关度: {{ (source.score * 100).toFixed(0) }}%</div>
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
  margin-bottom: 18px;
  padding: 6px 0;
  animation: rise-in 0.3s ease both;
}

.message-user {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 34px;
  height: 34px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
}

.message-user .message-avatar {
  background: var(--text-1);
  color: var(--bg-surface);
}

.message-assistant .message-avatar {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid var(--border);
}

.message-content {
  max-width: min(76%, 720px);
  padding: 12px 16px;
  font-size: 15px;
}

.message-user .message-content {
  background: var(--accent);
  color: var(--bg-surface);
  border-radius: var(--radius-lg) var(--radius-lg) var(--radius-sm) var(--radius-lg);
  box-shadow: var(--shadow-sm);
}

.message-user .message-content :deep(.citation) {
  color: var(--bg-surface);
  background: rgba(0, 0, 0, 0.18);
}

.message-assistant .message-content {
  background: var(--bg-surface);
  color: var(--text-1);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg) var(--radius-lg) var(--radius-lg) var(--radius-sm);
  box-shadow: var(--shadow-sm);
  line-height: 1.75;
}

.message-text {
  word-break: break-word;
}

.loading {
  display: flex;
  gap: 5px;
  padding: 8px 0;
}

.loading span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  animation: bounce 1.4s infinite ease-in-out both;
}

.loading span:nth-child(1) { animation-delay: -0.32s; }
.loading span:nth-child(2) { animation-delay: -0.16s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.stream-caret {
  display: inline-block;
  width: 9px;
  height: 1.15em;
  margin-left: 3px;
  vertical-align: text-bottom;
  background: var(--accent);
  border-radius: 1px;
  animation: caret-blink 1s steps(1) infinite;
}

.sources-panel {
  margin-top: 14px;
  padding: 14px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent-2);
  border-radius: var(--radius-sm);
}

.sources-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 13px;
  margin-bottom: 6px;
}

.sources-count {
  margin-left: auto;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-3);
}

.timing {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-3);
}

.source-item {
  padding: 10px 0;
  border-bottom: 1px dashed var(--border);
}

.source-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.source-file {
  font-size: 12.5px;
  color: var(--accent-2);
  font-weight: 600;
}

.source-chunk {
  font-size: 13px;
  margin: 5px 0;
  line-height: 1.65;
  color: var(--text-2);
}

.source-score {
  font-size: 11px;
  color: var(--text-3);
}

.citation {
  color: var(--accent-2);
  cursor: pointer;
  font-weight: 600;
  background: var(--accent-2-soft);
  border-radius: var(--radius-sm);
  padding: 0 4px;
  font-size: 0.88em;
  line-height: 1.4;
}
</style>
