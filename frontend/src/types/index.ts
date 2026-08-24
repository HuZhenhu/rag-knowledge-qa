// 对话消息
export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  timing?: Timing
  feedback?: 'positive' | 'negative' | null
  // Agent 推理过程（Agentic RAG 前端可视化，M5）
  // 兼容无该字段的旧消息渲染（设计文档 §4.2）
  agent_trace?: AgentTraceEvent[]
}

// Agent 推理过程事件（Agentic RAG 前端可视化，M5）
// 与后端 src/core/agentic 各节点写入的 trace 事件对齐（设计文档 §3.2 / §4.1）
export type AgentEventType =
  | 'user_question'
  | 'agent_plan'
  | 'agent_tool_call'
  | 'agent_evidence'
  | 'agent_reflect'
  | 'agent_final'

export interface AgentTraceEvent {
  node: string          // 产生事件的节点（supervisor / planner / retriever_agent / web_agent / critic / summarizer / start）
  event: AgentEventType // 事件类型
  [key: string]: any    // 事件附加字段（route / sub_questions / tool_calls / sources / decision / citations 等）
}

// 引用来源
export interface Source {
  file: string
  section: string
  content_type: string
  chunk: string
  score: number
}

// 耗时统计
export interface Timing {
  retrieval_ms: number
  generation_ms: number
  total_ms: number
}

// Token用量
export interface Usage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

// 问答响应
export interface QueryResponse {
  request_id: string
  answer: string
  sources: Source[]
  usage: Usage
  timing: Timing
}

// WebSocket消息类型
export interface WSTokenMessage {
  type: 'token'
  content: string
}

export interface WSSourcesMessage {
  type: 'sources'
  sources: Source[]
}

export interface WSDoneMessage {
  type: 'done'
  usage: Usage
  timing: Timing
}

export interface WSErrorMessage {
  type: 'error'
  message: string
}

// Agent 推理过程事件消息（Agentic RAG 前端可视化，M5，设计文档 §4.1）
// 普通引擎（langchain/original）不产生该类事件
export interface WSAgentEventMessage {
  type: AgentEventType
  message_id: string
  data: AgentTraceEvent
}

export type WSMessage = WSTokenMessage | WSSourcesMessage | WSDoneMessage | WSErrorMessage | WSAgentEventMessage

// 数据监控事件
export interface DataMonitorEvent {
  type: 'file_change' | 'index_start' | 'index_progress' | 'index_complete' | 'index_error' | 'pong'
  timestamp: number
}

export interface FileChangeEvent extends DataMonitorEvent {
  type: 'file_change'
  action: string
  files: string[]
}

export interface IndexStartEvent extends DataMonitorEvent {
  type: 'index_start'
  files: string[]
  count: number
}

export interface IndexProgressEvent extends DataMonitorEvent {
  type: 'index_progress'
  current: number
  total: number
  filename: string
}

export interface IndexCompleteEvent extends DataMonitorEvent {
  type: 'index_complete'
  stats: { added: number; updated: number; deleted: number; errors: number }
}

export interface IndexErrorEvent extends DataMonitorEvent {
  type: 'index_error'
  filename: string
  error: string
}
