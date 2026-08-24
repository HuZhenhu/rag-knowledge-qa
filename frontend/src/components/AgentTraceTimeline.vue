<script setup lang="ts">
import { ref, computed } from 'vue'
import type { AgentTraceEvent } from '../types'

/**
 * Agent 推理过程时间线折叠面板（Agentic RAG 前端可视化，M5，设计文档 §4.2）
 * 按时间线渲染 agent_plan / agent_tool_call / agent_evidence / agent_reflect / agent_final 事件。
 * 消息对象无 agent_trace 字段时不渲染本组件（兼容旧消息）。
 */
const props = defineProps<{
  trace: AgentTraceEvent[]
}>()

const expanded = ref(true)

// 节点名称映射（对齐后端 src/core/agentic 各节点）
const NODE_LABELS: Record<string, string> = {
  start: '用户提问',
  supervisor: '总调度 Supervisor',
  planner: '规划器 Planner',
  retriever_agent: '知识库检索 Agent',
  web_agent: '联网搜索 Agent',
  critic: '验证评审 Critic',
  summarizer: '汇总生成 Summarizer'
}

// 事件类型名称映射（对齐设计文档 §4.1）
const EVENT_LABELS: Record<string, string> = {
  user_question: '用户提问',
  agent_plan: '规划',
  agent_tool_call: '工具调用',
  agent_evidence: '证据',
  agent_reflect: '评审反思',
  agent_final: '最终答案'
}

function nodeLabel(node: string): string {
  return NODE_LABELS[node] || node
}

function eventLabel(event: string): string {
  return EVENT_LABELS[event] || event
}

// 当前活动节点：时间线最后一条事件所在节点
const activeNode = computed(() => {
  if (!props.trace.length) return ''
  return props.trace[props.trace.length - 1].node
})

// 反思次数：取最近一次 agent_reflect 的 retry_count
const retryCount = computed(() => {
  const reflects = props.trace.filter(t => t.event === 'agent_reflect')
  if (!reflects.length) return 0
  const last = reflects[reflects.length - 1]
  return typeof last.retry_count === 'number' ? last.retry_count : 0
})

// ---- 详情渲染辅助 ----
function planText(p: any): string {
  const parts: string[] = []
  if (p && p.question) parts.push(p.question)
  if (p && p.intent) parts.push(`（${p.intent}）`)
  if (p && Array.isArray(p.tools) && p.tools.length) parts.push(`工具：${p.tools.join(' / ')}`)
  return parts.join(' ')
}

function paramText(params: any): string {
  if (!params || typeof params !== 'object') return ''
  const list = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
  return list.length ? `参数 { ${list.join(', ')} }` : ''
}

function issueText(issue: any): string {
  if (!issue) return ''
  const sev = issue.severity === 'error' ? '[严重] ' : issue.severity === 'warning' ? '[提示] ' : ''
  return `${sev}${issue.detail || issue.type || ''}`
}

// 证据来源（兼容字符串或对象两种后端形态）
function evidenceFile(ev: any): string {
  if (typeof ev === 'string') return ev
  const meta = (ev && ev.metadata) || {}
  return meta.source_file || (ev && ev.file) || '未知来源'
}

function evidenceSection(ev: any): string {
  if (typeof ev === 'string') return ''
  const meta = (ev && ev.metadata) || {}
  return meta.section || (ev && ev.section) || ''
}

function citationText(c: any): string {
  if (!c) return ''
  const parts: string[] = []
  if (c.file || c.source_file) parts.push(c.file || c.source_file)
  if (c.section) parts.push(`§ ${c.section}`)
  if (c.source_url) parts.push(c.source_url)
  if (c.text) parts.push(String(c.text).length > 80 ? String(c.text).slice(0, 80) + '…' : String(c.text))
  return parts.join(' — ')
}
</script>

<template>
  <div class="agent-trace">
    <!-- 折叠面板头部 -->
    <div class="agent-trace-header" @click="expanded = !expanded">
      <span class="agent-trace-title">Agent 推理过程</span>
      <span v-if="retryCount > 0" class="retry-badge">反思 {{ retryCount }} 次</span>
      <span class="event-count">{{ trace.length }} 个事件</span>
      <span class="toggle">{{ expanded ? '收起' : '展开' }}</span>
    </div>

    <!-- 时间线主体 -->
    <div v-if="expanded" class="agent-trace-body">
      <div
        v-for="(ev, idx) in trace"
        :key="idx"
        class="trace-item"
        :class="{ 'is-active': ev.node === activeNode && ev.event !== 'agent_final' }"
      >
        <div class="trace-node">
          <span class="node-dot" :class="ev.event"></span>
          <span class="node-name">{{ nodeLabel(ev.node) }}</span>
          <span class="event-tag" :class="ev.event">{{ eventLabel(ev.event) }}</span>
          <span v-if="ev.node === activeNode" class="active-badge">当前</span>
        </div>

        <div class="trace-detail">
          <!-- 用户提问 -->
          <div v-if="ev.event === 'user_question'" class="detail-line">
            问题：{{ ev.question }}
          </div>

          <!-- 规划（Supervisor 路由决策 / Planner 子问题拆解） -->
          <template v-if="ev.event === 'agent_plan'">
            <div v-if="ev.route" class="detail-line">
              路由决策：<strong class="route">{{ ev.route }}</strong>
              <span v-if="ev.reason" class="detail-sub">（{{ ev.reason }}）</span>
            </div>
            <div v-if="ev.sub_questions && ev.sub_questions.length" class="detail-list">
              <div class="detail-label">子问题拆解：</div>
              <div v-for="(sq, i) in ev.sub_questions" :key="i" class="detail-item">• {{ sq }}</div>
            </div>
            <div v-if="ev.plan && ev.plan.length" class="detail-list">
              <div class="detail-label">计划：</div>
              <div v-for="(p, i) in ev.plan" :key="i" class="detail-item">• {{ planText(p) }}</div>
            </div>
            <div v-if="ev.note" class="detail-line note-text">{{ ev.note }}</div>
          </template>

          <!-- 工具调用 -->
          <template v-if="ev.event === 'agent_tool_call'">
            <div v-if="ev.tool_calls && ev.tool_calls.length" class="detail-list">
              <div v-for="(tc, i) in ev.tool_calls" :key="i" class="tool-call">
                <span class="tool-name">{{ tc.tool }}</span>
                <span v-if="tc.error" class="tool-error">调用失败：{{ tc.error }}</span>
                <span v-else-if="tc.params" class="tool-params">{{ paramText(tc.params) }}</span>
                <span v-if="tc.hits !== undefined" class="tool-hits">命中 {{ tc.hits }} 条</span>
              </div>
            </div>
            <div v-if="ev.tool" class="detail-line">
              工具：<span class="tool-name">{{ ev.tool }}</span>
              <span v-if="ev.query" class="detail-sub">查询：{{ ev.query }}</span>
              <span v-if="ev.hits !== undefined" class="tool-hits">命中 {{ ev.hits }} 条</span>
            </div>
            <div v-if="ev.note" class="detail-line note-text">{{ ev.note }}</div>
          </template>

          <!-- 证据卡片（来源 + 章节） -->
          <template v-if="ev.event === 'agent_evidence'">
            <div v-if="ev.evidence_count !== undefined" class="detail-line">
              证据 {{ ev.evidence_count }} 条
            </div>
            <div v-if="ev.sources && ev.sources.length" class="evidence-cards">
              <div v-for="(s, i) in ev.sources" :key="i" class="evidence-card">
                <div class="evidence-file">{{ evidenceFile(s) }}</div>
                <div v-if="evidenceSection(s)" class="evidence-section">章节：{{ evidenceSection(s) }}</div>
              </div>
            </div>
            <div v-if="ev.evidences && ev.evidences.length" class="evidence-cards">
              <div v-for="(e, i) in ev.evidences" :key="i" class="evidence-card">
                <div class="evidence-file">{{ evidenceFile(e) }}</div>
                <div v-if="evidenceSection(e)" class="evidence-section">章节：{{ evidenceSection(e) }}</div>
                <div v-if="e.source_url" class="evidence-url">{{ e.source_url }}</div>
              </div>
            </div>
          </template>

          <!-- Critic 评审结论与反思 -->
          <template v-if="ev.event === 'agent_reflect'">
            <div class="detail-line">
              评审结论：
              <strong :class="ev.decision === 'pass' ? 'decision-pass' : 'decision-retry'">
                {{ ev.decision === 'pass' ? '通过' : '需反思重试' }}
              </strong>
              <span v-if="ev.retry_count !== undefined" class="detail-sub">（反思 {{ ev.retry_count }} 次）</span>
            </div>
            <div v-if="ev.reflection" class="detail-line">{{ ev.reflection }}</div>
            <div v-if="ev.issues && ev.issues.length" class="detail-list">
              <div class="detail-label">问题清单：</div>
              <div v-for="(issue, i) in ev.issues" :key="i" class="detail-item">• {{ issueText(issue) }}</div>
            </div>
          </template>

          <!-- 最终依据链路 -->
          <template v-if="ev.event === 'agent_final'">
            <div v-if="ev.citations && ev.citations.length" class="detail-list">
              <div class="detail-label">最终依据链路：</div>
              <div v-for="(c, i) in ev.citations" :key="i" class="citation-item">[{{ Number(i) + 1 }}] {{ citationText(c) }}</div>
            </div>
            <div v-if="ev.answer" class="detail-line answer-snippet">{{ ev.answer }}</div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-trace {
  margin-top: 12px;
  border: 1px solid var(--el-border-color-lighter, #dcdfe6);
  border-radius: 8px;
  background: var(--el-bg-color, #fff);
  overflow: hidden;
}

.agent-trace-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  background: var(--el-fill-color-light, #f5f7fa);
  font-size: 13px;
  user-select: none;
}

.agent-trace-title {
  font-weight: 600;
}

.retry-badge {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 10px;
  background: var(--el-color-warning-bg-color, #fdf6ec);
  color: var(--el-color-warning, #e6a23c);
}

.event-count {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}

.toggle {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-color-primary, #409eff);
}

.agent-trace-body {
  max-height: 320px;
  overflow-y: auto;
  padding: 8px 0;
}

.trace-item {
  position: relative;
  padding: 8px 14px 8px 34px;
  border-left: 2px solid transparent;
}

.trace-item.is-active {
  border-left-color: var(--el-color-primary, #409eff);
  background: var(--el-color-primary-light-9, #ecf5ff);
}

.trace-node {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.node-dot {
  position: absolute;
  left: 12px;
  top: 14px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #909399;
}

.node-dot.agent_plan { background: #409eff; }
.node-dot.agent_tool_call { background: #e6a23c; }
.node-dot.agent_evidence { background: #67c23a; }
.node-dot.agent_reflect { background: #f56c6c; }
.node-dot.agent_final { background: #909399; }
.node-dot.user_question { background: #909399; }

.node-name {
  font-weight: 600;
  color: var(--el-text-color-primary, #303133);
}

.event-tag {
  font-size: 11px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--el-fill-color, #f0f2f5);
  color: var(--el-text-color-regular, #606266);
}

.event-tag.agent_plan { background: var(--el-color-primary-light-8, #d9ecff); color: var(--el-color-primary, #409eff); }
.event-tag.agent_tool_call { background: var(--el-color-warning-light-8, #fdf0d8); color: var(--el-color-warning, #e6a23c); }
.event-tag.agent_evidence { background: var(--el-color-success-light-8, #e1f3d8); color: var(--el-color-success, #67c23a); }
.event-tag.agent_reflect { background: var(--el-color-danger-light-8, #fde2e2); color: var(--el-color-danger, #f56c6c); }

.active-badge {
  font-size: 10px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--el-color-primary, #409eff);
  color: #fff;
}

.trace-detail {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-regular, #606266);
  line-height: 1.6;
}

.detail-line { margin: 2px 0; }
.detail-sub { color: var(--el-text-color-secondary, #909399); }
.note-text { color: var(--el-text-color-secondary, #909399); font-style: italic; }
.route { color: var(--el-color-primary, #409eff); }

.detail-list { margin: 2px 0; }
.detail-label { font-weight: 600; margin: 4px 0 2px; }
.detail-item { margin: 1px 0; word-break: break-all; }

.tool-call { margin: 2px 0; }
.tool-name {
  display: inline-block;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--el-color-warning-light-9, #fdf6ec);
  color: var(--el-color-warning, #e6a23c);
  font-weight: 600;
}
.tool-params { margin-left: 4px; color: var(--el-text-color-secondary, #909399); }
.tool-error { margin-left: 4px; color: var(--el-color-danger, #f56c6c); }
.tool-hits { margin-left: 4px; color: var(--el-color-success, #67c23a); }

.evidence-cards { display: flex; flex-direction: column; gap: 4px; margin: 4px 0; }
.evidence-card {
  padding: 6px 10px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 6px;
  background: var(--el-color-success-light-9, #f0f9eb);
}
.evidence-file { font-weight: 600; color: var(--el-color-success, #67c23a); font-size: 12px; }
.evidence-section { font-size: 11px; color: var(--el-text-color-secondary, #909399); }
.evidence-url { font-size: 11px; color: var(--el-color-primary, #409eff); word-break: break-all; }

.decision-pass { color: var(--el-color-success, #67c23a); }
.decision-retry { color: var(--el-color-danger, #f56c6c); }

.citation-item {
  padding: 4px 8px;
  margin: 2px 0;
  border-left: 3px solid var(--el-color-primary, #409eff);
  background: var(--el-color-primary-light-9, #ecf5ff);
  word-break: break-all;
}

.answer-snippet {
  margin-top: 4px;
  padding: 6px 10px;
  background: var(--el-fill-color-light, #f5f7fa);
  border-radius: 6px;
  max-height: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
