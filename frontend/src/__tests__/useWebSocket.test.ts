import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useWebSocket } from '../composables/useWebSocket'

// Mock WebSocket
let mockWebSocket: any

const MockWebSocket = vi.fn(() => mockWebSocket) as any
MockWebSocket.OPEN = 1
MockWebSocket.CLOSED = 3
vi.stubGlobal('WebSocket', MockWebSocket)

describe('useWebSocket', () => {
  let ws: ReturnType<typeof useWebSocket>

  beforeEach(() => {
    vi.clearAllMocks()
    mockWebSocket = {
      send: vi.fn(),
      close: vi.fn(),
      readyState: 1,
      onmessage: null,
      onclose: null,
      onerror: null,
      onopen: null
    }
  })

  it('initializes with disconnected state', () => {
    ws = useWebSocket('ws://localhost:8080')
    expect(ws.isConnected.value).toBe(false)
  })

  it('connects to websocket server', () => {
    ws = useWebSocket('ws://localhost:8080')
    ws.connect('session123')

    expect(WebSocket).toHaveBeenCalledWith('ws://localhost:8080?session_id=session123')
  })

  it('sends query message', () => {
    ws = useWebSocket('ws://localhost:8080')
    ws.connect('session123')

    ws.sendQuery('测试问题', 'msg1')

    expect(mockWebSocket.send).toHaveBeenCalledWith(JSON.stringify({
      type: 'query',
      query: '测试问题',
      message_id: 'msg1'
    }))
  })

  it('handles incoming token messages', () => {
    const onToken = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onToken = onToken
    ws.connect('session123')

    const event = {
      data: JSON.stringify({
        type: 'token',
        message_id: 'msg1',
        token: '你好'
      })
    }
    mockWebSocket.onmessage(event)

    expect(onToken).toHaveBeenCalledWith('msg1', '你好')
  })

  it('handles incoming sources messages', () => {
    const onSources = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onSources = onSources
    ws.connect('session123')

    const sources = [{ file: 'test.md', section: 'test', content_type: 'text', chunk: 'test', score: 0.9 }]
    const event = {
      data: JSON.stringify({
        type: 'sources',
        message_id: 'msg1',
        sources
      })
    }
    mockWebSocket.onmessage(event)

    expect(onSources).toHaveBeenCalledWith('msg1', sources)
  })

  it('handles done messages', () => {
    const onDone = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onDone = onDone
    ws.connect('session123')

    const event = {
      data: JSON.stringify({
        type: 'done',
        message_id: 'msg1',
        timing: { retrieval_ms: 100, generation_ms: 200, total_ms: 300 }
      })
    }
    mockWebSocket.onmessage(event)

    expect(onDone).toHaveBeenCalledWith('msg1', { retrieval_ms: 100, generation_ms: 200, total_ms: 300 })
  })

  it('handles error messages', () => {
    const onError = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onError = onError
    ws.connect('session123')

    const event = {
      data: JSON.stringify({
        type: 'error',
        message: '发生错误'
      })
    }
    mockWebSocket.onmessage(event)

    expect(onError).toHaveBeenCalledWith('发生错误')
  })

  it('disconnects from server', () => {
    ws = useWebSocket('ws://localhost:8080')
    ws.connect('session123')
    ws.disconnect()

    expect(mockWebSocket.close).toHaveBeenCalled()
  })

  // ---- Agent 推理过程事件（Agentic RAG 前端可视化，M5，设计文档 §4.1）----
  it('handles agent_plan event', () => {
    const onAgentPlan = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onAgentPlan = onAgentPlan
    ws.connect('session123')

    const eventData = { node: 'supervisor', event: 'agent_plan', route: 'retrieve', reason: '需要检索知识库' }
    const event = { data: JSON.stringify({ type: 'agent_plan', message_id: 'msg1', data: eventData }) }
    mockWebSocket.onmessage(event)

    expect(onAgentPlan).toHaveBeenCalledWith('msg1', eventData)
  })

  it('handles agent_tool_call event', () => {
    const onAgentToolCall = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onAgentToolCall = onAgentToolCall
    ws.connect('session123')

    const eventData = { node: 'retriever_agent', event: 'agent_tool_call', tool_calls: [{ tool: 'kb_search_keyword', hits: 3 }] }
    const event = { data: JSON.stringify({ type: 'agent_tool_call', message_id: 'msg1', data: eventData }) }
    mockWebSocket.onmessage(event)

    expect(onAgentToolCall).toHaveBeenCalledWith('msg1', eventData)
  })

  it('handles agent_evidence event', () => {
    const onAgentEvidence = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onAgentEvidence = onAgentEvidence
    ws.connect('session123')

    const eventData = {
      node: 'retriever_agent',
      event: 'agent_evidence',
      evidence_count: 2,
      sources: [{ file: 'rag.md', section: '简介' }]
    }
    const event = { data: JSON.stringify({ type: 'agent_evidence', message_id: 'msg1', data: eventData }) }
    mockWebSocket.onmessage(event)

    expect(onAgentEvidence).toHaveBeenCalledWith('msg1', eventData)
  })

  it('handles agent_reflect event', () => {
    const onAgentReflect = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onAgentReflect = onAgentReflect
    ws.connect('session123')

    const eventData = { node: 'critic', event: 'agent_reflect', decision: 'retry', retry_count: 1 }
    const event = { data: JSON.stringify({ type: 'agent_reflect', message_id: 'msg1', data: eventData }) }
    mockWebSocket.onmessage(event)

    expect(onAgentReflect).toHaveBeenCalledWith('msg1', eventData)
  })

  it('handles agent_final event', () => {
    const onAgentFinal = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onAgentFinal = onAgentFinal
    ws.connect('session123')

    const eventData = {
      node: 'summarizer',
      event: 'agent_final',
      answer: 'RAG 是...',
      citations: [{ file: 'rag.md', section: '简介' }]
    }
    const event = { data: JSON.stringify({ type: 'agent_final', message_id: 'msg1', data: eventData }) }
    mockWebSocket.onmessage(event)

    expect(onAgentFinal).toHaveBeenCalledWith('msg1', eventData)
  })

  it('does not trigger agent callbacks for non-agent messages', () => {
    const onAgentPlan = vi.fn()
    ws = useWebSocket('ws://localhost:8080')
    ws.onAgentPlan = onAgentPlan
    ws.connect('session123')

    // 普通引擎只推 token / sources / done / error，不应触发 agent 回调
    const event = { data: JSON.stringify({ type: 'token', message_id: 'msg1', token: '你好' }) }
    mockWebSocket.onmessage(event)

    expect(onAgentPlan).not.toHaveBeenCalled()
  })
})
