import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AgentTraceTimeline from '../components/AgentTraceTimeline.vue'
import type { AgentTraceEvent } from '../types'

describe('AgentTraceTimeline', () => {
  // 完整推理链路（对齐设计文档 §3.2 各节点 trace）
  const fullTrace: AgentTraceEvent[] = [
    { node: 'start', event: 'user_question', question: '什么是RAG？' },
    { node: 'supervisor', event: 'agent_plan', route: 'retrieve', reason: '需要检索知识库' },
    {
      node: 'planner',
      event: 'agent_plan',
      sub_questions: ['RAG 的定义', 'RAG 的应用'],
      plan: [
        { question: 'RAG 的定义', tools: ['kb_search_keyword'] },
        { question: 'RAG 的应用', tools: ['kb_search_keyword'] }
      ]
    },
    {
      node: 'retriever_agent',
      event: 'agent_tool_call',
      tool_calls: [{ tool: 'kb_search_keyword', params: { query: 'RAG 的定义' }, hits: 3 }]
    },
    {
      node: 'retriever_agent',
      event: 'agent_evidence',
      evidence_count: 2,
      sources: [{ file: 'rag.md', section: '简介' }, { file: 'rag2.md', section: '应用' }]
    },
    { node: 'critic', event: 'agent_reflect', decision: 'pass', reflection: '证据充分', issues: [], retry_count: 0 },
    {
      node: 'summarizer',
      event: 'agent_final',
      answer: 'RAG 是检索增强生成',
      citations: [{ file: 'rag.md', section: '简介' }]
    }
  ]

  it('renders timeline header with event count', () => {
    const wrapper = mount(AgentTraceTimeline, { props: { trace: fullTrace } })
    expect(wrapper.find('.agent-trace').exists()).toBe(true)
    expect(wrapper.find('.agent-trace-title').text()).toBe('Agent 推理过程')
    expect(wrapper.find('.event-count').text()).toContain('7')
  })

  it('renders user question, plan, tool call, evidence, reflect, final nodes', () => {
    const wrapper = mount(AgentTraceTimeline, { props: { trace: fullTrace } })
    const text = wrapper.text()
    expect(text).toContain('用户提问')
    expect(text).toContain('总调度 Supervisor')
    expect(text).toContain('规划器 Planner')
    expect(text).toContain('知识库检索 Agent')
    expect(text).toContain('验证评审 Critic')
    expect(text).toContain('汇总生成 Summarizer')
    // 规划详情
    expect(text).toContain('子问题拆解')
    expect(text).toContain('RAG 的定义')
    // 工具调用
    expect(text).toContain('kb_search_keyword')
    // 证据卡片（来源 + 章节）
    expect(text).toContain('rag.md')
    expect(text).toContain('章节：简介')
    expect(text).toContain('章节：应用')
    // Critic 结论
    expect(text).toContain('评审结论')
    expect(text).toContain('通过')
    // 最终依据链路
    expect(text).toContain('最终依据链路')
  })

  it('shows retry badge and decision when retry occurred', () => {
    const retryTrace: AgentTraceEvent[] = [
      { node: 'supervisor', event: 'agent_plan', route: 'retrieve' },
      { node: 'retriever_agent', event: 'agent_evidence', evidence_count: 0, sources: [] },
      { node: 'critic', event: 'agent_reflect', decision: 'retry', reflection: '缺少证据', issues: [{ type: 'coverage', severity: 'error', detail: '缺少证据' }], retry_count: 1 },
      { node: 'summarizer', event: 'agent_final', answer: '未找到', citations: [] }
    ]
    const wrapper = mount(AgentTraceTimeline, { props: { trace: retryTrace } })
    expect(wrapper.find('.retry-badge').exists()).toBe(true)
    expect(wrapper.find('.retry-badge').text()).toContain('反思 1 次')
    expect(wrapper.text()).toContain('需反思重试')
    expect(wrapper.text()).toContain('问题清单')
  })

  it('shows active badge on current node', () => {
    const wrapper = mount(AgentTraceTimeline, { props: { trace: fullTrace } })
    expect(wrapper.findAll('.active-badge').length).toBe(1)
  })

  it('collapses and expands timeline body', async () => {
    const wrapper = mount(AgentTraceTimeline, { props: { trace: fullTrace } })
    expect(wrapper.find('.agent-trace-body').exists()).toBe(true)

    await wrapper.find('.agent-trace-header').trigger('click')
    expect(wrapper.find('.agent-trace-body').exists()).toBe(false)

    await wrapper.find('.agent-trace-header').trigger('click')
    expect(wrapper.find('.agent-trace-body').exists()).toBe(true)
  })

  it('renders evidence as file name strings (legacy shape)', () => {
    // 兼容旧形态：sources 为文件名列表（字符串）
    const legacyEvidence: AgentTraceEvent[] = [
      { node: 'retriever_agent', event: 'agent_evidence', evidence_count: 1, sources: ['legacy.md'] }
    ]
    const wrapper = mount(AgentTraceTimeline, { props: { trace: legacyEvidence } })
    expect(wrapper.text()).toContain('legacy.md')
  })
})
