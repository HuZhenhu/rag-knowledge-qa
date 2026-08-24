import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatMessage from '../components/ChatMessage.vue'
import type { Message } from '../types'

describe('ChatMessage', () => {
  const userMessage: Message = {
    id: '1',
    role: 'user',
    content: '什么是RAG？'
  }

  const assistantMessage: Message = {
    id: '2',
    role: 'assistant',
    content: 'RAG是检索增强生成技术[1]',
    sources: [
      { file: 'rag.md', section: '简介', content_type: 'text', chunk: 'RAG是一种...', score: 0.95 }
    ],
    timing: { retrieval_ms: 100, generation_ms: 200, total_ms: 300 }
  }

  it('renders user message', () => {
    const wrapper = mount(ChatMessage, { props: { message: userMessage } })
    expect(wrapper.text()).toContain('什么是RAG？')
    expect(wrapper.classes()).toContain('message-user')
  })

  it('renders assistant message', () => {
    const wrapper = mount(ChatMessage, { props: { message: assistantMessage } })
    expect(wrapper.text()).toContain('RAG是检索增强生成技术')
    expect(wrapper.classes()).toContain('message-assistant')
  })

  it('renders citation references', () => {
    const wrapper = mount(ChatMessage, { props: { message: assistantMessage } })
    expect(wrapper.find('.citation').exists()).toBe(true)
    expect(wrapper.text()).toContain('[1]')
  })

  it('expands sources on message content click', async () => {
    const wrapper = mount(ChatMessage, { props: { message: assistantMessage } })
    const content = wrapper.find('.message-content')
    await content.trigger('click')

    expect(wrapper.find('.sources-panel').exists()).toBe(true)
    expect(wrapper.text()).toContain('rag.md')
    expect(wrapper.text()).toContain('RAG是一种...')
  })

  it('shows timing info', async () => {
    const wrapper = mount(ChatMessage, { props: { message: assistantMessage } })
    const content = wrapper.find('.message-content')
    await content.trigger('click')

    expect(wrapper.text()).toContain('300ms')
  })

  it('shows loading indicator for empty assistant', () => {
    const loadingMessage: Message = { id: '3', role: 'assistant', content: '' }
    const wrapper = mount(ChatMessage, { props: { message: loadingMessage } })
    expect(wrapper.find('.loading').exists()).toBe(true)
  })

  // ---- Agent 推理过程时间线（Agentic RAG 前端可视化，M5，设计文档 §4.2）----
  it('renders agent trace timeline for agentic messages', () => {
    const agenticMessage: Message = {
      id: '4',
      role: 'assistant',
      content: 'RAG 是检索增强生成[1]',
      agent_trace: [
        { node: 'supervisor', event: 'agent_plan', route: 'retrieve', reason: '需要检索知识库' },
        { node: 'retriever_agent', event: 'agent_tool_call', tool_calls: [{ tool: 'kb_search_keyword', hits: 3 }] },
        { node: 'retriever_agent', event: 'agent_evidence', evidence_count: 2, sources: [{ file: 'rag.md', section: '简介' }] },
        { node: 'critic', event: 'agent_reflect', decision: 'pass', retry_count: 0 },
        { node: 'summarizer', event: 'agent_final', answer: 'RAG 是...', citations: [{ file: 'rag.md', section: '简介' }] }
      ]
    }
    const wrapper = mount(ChatMessage, { props: { message: agenticMessage } })

    // 时间线面板存在且渲染关键内容
    expect(wrapper.find('.agent-trace').exists()).toBe(true)
    expect(wrapper.text()).toContain('Agent 推理过程')
    expect(wrapper.text()).toContain('总调度 Supervisor')
    expect(wrapper.text()).toContain('路由决策')
    expect(wrapper.text()).toContain('retrieve')
    expect(wrapper.text()).toContain('知识库检索 Agent')
    expect(wrapper.text()).toContain('kb_search_keyword')
    expect(wrapper.text()).toContain('rag.md')
    expect(wrapper.text()).toContain('章节：简介')
    expect(wrapper.text()).toContain('验证评审 Critic')
    expect(wrapper.text()).toContain('评审结论')
    expect(wrapper.text()).toContain('最终依据链路')
  })

  it('does not render agent trace for messages without agent_trace (legacy compat)', () => {
    const legacyMessage: Message = {
      id: '5',
      role: 'assistant',
      content: '这是普通引擎的回答[1]',
      sources: [{ file: 'rag.md', section: '简介', content_type: 'text', chunk: '内容', score: 0.9 }]
    }
    const wrapper = mount(ChatMessage, { props: { message: legacyMessage } })

    expect(wrapper.find('.agent-trace').exists()).toBe(false)
    // 普通消息渲染不受影响
    expect(wrapper.text()).toContain('这是普通引擎的回答')
  })
})
