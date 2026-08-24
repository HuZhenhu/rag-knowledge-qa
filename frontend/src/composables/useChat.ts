import { ref } from 'vue'
import type { Message, Timing, AgentTraceEvent } from '../types'

/**
 * 生成唯一ID
 */
function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).substr(2)
}

/**
 * 聊天组合式函数
 * 管理消息状态和WebSocket通信
 */
export function useChat() {
  const messages = ref<Message[]>([])
  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const sessionId = ref<string>(generateId())

  /**
   * 发送用户消息
   */
  function sendMessage(content: string): void {
    if (!content.trim()) return

    // 添加用户消息
    const userMessage: Message = {
      id: generateId(),
      role: 'user',
      content: content.trim()
    }
    messages.value.push(userMessage)

    // 设置loading状态
    isLoading.value = true
    error.value = null
  }

  /**
   * 添加assistant消息（WebSocket收到响应时调用）
   */
  function addAssistantMessage(): Message {
    const assistantMessage: Message = {
      id: generateId(),
      role: 'assistant',
      content: ''
    }
    messages.value.push(assistantMessage)
    return assistantMessage
  }

  /**
   * 清空消息
   */
  function clearMessages(): void {
    messages.value = []
    isLoading.value = false
    error.value = null
    sessionId.value = generateId()
  }

  /**
   * 处理收到的token
   */
  function handleToken(messageId: string, token: string): void {
    const msg = messages.value.find(m => m.id === messageId)
    if (msg) {
      msg.content += token
    }
  }

  /**
   * 设置消息完成
   * @param agentTrace Agent 推理过程事件（Agentic RAG 前端可视化，M5）
   *                   仅 agentic 引擎下产生；普通引擎不传，消息保持无该字段
   */
  function setMessageDone(
    messageId: string,
    sources?: Message['sources'],
    timing?: Timing,
    agentTrace?: AgentTraceEvent[]
  ): void {
    const msg = messages.value.find(m => m.id === messageId)
    if (msg) {
      if (sources) msg.sources = sources
      if (timing) msg.timing = timing
      if (agentTrace && agentTrace.length > 0) msg.agent_trace = agentTrace
    }
    isLoading.value = false
  }

  /**
   * 设置错误
   */
  function setError(err: string): void {
    error.value = err
    isLoading.value = false
  }

  return {
    messages,
    isLoading,
    error,
    sessionId,
    sendMessage,
    addAssistantMessage,
    clearMessages,
    handleToken,
    setMessageDone,
    setError
  }
}
