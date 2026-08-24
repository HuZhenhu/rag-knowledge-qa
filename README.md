# RAG 智能问答系统

企业级知识库问答助手。用户提问后，系统从知识库检索相关文档片段，基于检索结果生成带引用标注的回答。

## 功能特性

### 核心 RAG
- **多格式文档处理** — 支持 Markdown / TXT / DOCX / PDF / XLSX / 图片，Loader 插件化
- **智能切片** — 按标题切分，表格/图片整体保留，短 section 自动合并
- **混合检索** — 向量检索 + BM25 关键词检索，RRF 融合排序
- **查询理解** — 查询扩展、HyDE、意图分类
- **多轮对话** — SessionManager 管理会话历史，支持上下文追问
- **引用可追溯** — 回答中标注来源（文件名 + 章节名）

### Agentic RAG（RAG_ENGINE=agentic，M10 新增）
- **Supervisor 多 Agent 编排** — 基于 LangGraph 实现规划拆解 → 工具调用 → Critic 验证纠错 → Summarizer 汇总的完整推理链路
- **规划拆解** — Supervisor 路由 + Planner 将复杂问题拆分为子问题并制定检索计划
- **多源工具调用** — 知识库混合检索（向量+BM25）与联网搜索（Tavily）按需选择
- **Critic 验证纠错** — 对检索证据进行反思与质量校验，证据不足时自动重试（带 retry 上限）
- **推理过程可视化** — 前端时间线面板展示 Agent 规划 / 工具调用 / 证据 / 反思 / 最终回答全过程
- **评测兼容** — 同一套 evaluate.py 跑通 250 例评测集，新增规划正确率 / 工具选择合理性 / 平均反思次数 / 最终引用正确率四个专项维度

### 管理后台（Vue 3）
- **仪表盘** — 系统概览、统计卡片、最近文档、告警状态
- **文档管理** — 知识库文档列表、上传、删除
- **索引监控** — ChromaDB 索引状态、同步操作
- **查询日志** — 用户查询记录、延迟统计
- **评测** — RAG 质量评测结果展示
- **用户管理** — 用户列表、角色管理
- **聊天** — WebSocket 实时问答
- **国际化** — 中/英双语切换
- **主题切换** — 深色/浅色模式

### 安全防护
- **Prompt 注入防护** — 30+ 条中英文检测模式，拦截恶意指令
- **文件内容预检查** — 魔数签名验证，防止文件伪装
- **内容防泄露** — 拦截批量导出查询，保护知识库数据
- **输出清洗** — 过滤敏感内容，防止信息泄露

### 生产级能力
- **JWT 认证** — 登录注册、Token 刷新、角色权限
- **限流** — API 请求频率控制
- **结构化日志** — 请求追踪、审计日志
- **指标监控** — Prometheus 指标导出
- **告警** — 系统异常自动告警
- **WebSocket 数据监控** — 文件变化、索引进度实时推送

## 技术栈

| 组件 | 技术 |
|------|------|
| **LLM应用框架** | **LangChain**（业界主流）+ **LangGraph**（Agentic 多 Agent 编排） |
| LLM | DeepSeek / OpenAI / Anthropic Claude（可切换） |
| Embedding | sentence-transformers / OpenAI Embeddings（可切换） |
| 向量数据库 | ChromaDB（langchain-chroma 集成） |
| 关键词检索 | BM25Retriever + EnsembleRetriever 混合检索 |
| Prompt管理 | ChatPromptTemplate（LangChain组件） |
| 前端 | Vue 3 + Vite + TypeScript + Element Plus |
| 状态管理 | Pinia |
| 路由 | vue-router（含权限守卫） |
| 国际化 | vue-i18n |
| API 服务 | FastAPI + uvicorn + WebSocket |
| 数据库 | SQLite（用户、会话、日志） |

## 快速开始

### 环境要求

- Python 3.12+
- Node.js 18+
- [uv](https://docs.astral.sh/uv/)（Python 包管理）

### 安装

```bash
# 克隆仓库
git clone https://github.com/Realhu-555/rag-knowledge-qa.git
cd rag-knowledge-qa

# 创建虚拟环境并安装依赖
uv venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt

# 配置 API 密钥
cp .env.example .env
# 编辑 .env 填入 DeepSeek API Key
```

### 构建索引

```bash
python build_index.py
# 全量重建：python build_index.py --full
```

### 启动服务

```bash
# 启动后端 API（http://localhost:8080）
python main.py

# 启动前端开发服务器（http://localhost:5173）
cd frontend && npm install && npm run dev
```

### 登录系统

1. 打开 http://localhost:5173/login
2. 输入用户名和密码（首次使用需先注册）
3. 登录后可访问所有功能

## 项目结构

```
rag-knowledge-qa/
├── src/
│   ├── api/               # FastAPI 接口层
│   │   ├── routes.py      # API 路由
│   │   ├── auth.py        # API Key 鉴权
│   │   ├── jwt_auth.py    # JWT 认证
│   │   ├── rate_limit.py  # 限流中间件
│   │   ├── validation.py  # 安全验证（注入防护、文件检查）
│   │   └── schemas.py     # Pydantic 数据模型
│   ├── core/              # RAG 核心逻辑
│   │   ├── agentic/       # Agentic RAG 引擎（LangGraph Supervisor 多 Agent，M10）
│   │   │   ├── __init__.py # AgenticEngine 入口（query/query_stream）
│   │   │   ├── state.py    # AgentState（trace/tool_calls/retry_count/citations）
│   │   │   ├── graph.py    # LangGraph 图编排（start→supervisor→planner→retriever→critic→summarizer）
│   │   │   ├── supervisor_agent.py # 路由决策
│   │   │   ├── planner_agent.py    # 问题拆解与规划
│   │   │   ├── retriever_agent.py  # 知识库/联网工具调用
│   │   │   ├── critic_agent.py     # 反思校验与重试决策
│   │   │   ├── summarizer_agent.py # 汇总生成
│   │   │   └── web_agent.py        # Tavily 联网搜索
│   │   ├── loaders/       # 文档加载器（md/txt/docx/pdf/图片）
│   │   ├── splitter.py    # 智能切片
│   │   ├── embedder.py    # Embedding
│   │   ├── vector_store.py # ChromaDB 封装
│   │   ├── retriever.py   # 混合检索（向量+BM25）
│   │   ├── reranker.py    # CrossEncoder 重排序
│   │   ├── generator.py   # LLM 生成（带引用+安全指令）
│   │   ├── rag_engine.py  # 原版 RAG 引擎
│   │   ├── langchain_rag.py  # LangChain RAG 引擎（默认）
│   │   ├── session.py     # 多轮对话管理
│   │   ├── query_understander.py # 查询理解
│   │   ├── data_monitor.py # WebSocket 数据监控
│   │   ├── eval_scheduler.py # 评测定时任务
│   │   └── watcher.py     # 文件监听器
│   ├── storage/           # SQLite 持久化
│   │   ├── database.py    # 数据库操作
│   │   └── models.py      # 数据模型
│   └── config.py          # 配置管理
├── frontend/              # Vue 3 管理后台
│   ├── src/
│   │   ├── api/           # HTTP 客户端
│   │   ├── i18n/          # 国际化（中/英）
│   │   ├── layouts/       # 布局组件
│   │   ├── router/        # 路由配置
│   │   ├── stores/        # Pinia 状态管理
│   │   └── views/         # 页面组件
│   └── package.json
├── data/                  # 知识库文件
├── evaluation/            # 评测集
├── tests/                 # 测试用例
│   ├── test_prompt_security.py      # Prompt 注入防护测试
│   ├── test_file_preflight.py       # 文件预检查测试
│   └── test_content_leak_prevention.py # 内容防泄露测试
├── docs/                  # 项目文档
│   └── v1.1.0-changelog.md # 版本更新说明
├── main.py                # FastAPI 启动入口
├── build_index.py         # 构建向量索引
└── evaluate.py            # 评测脚本
```

## 配置说明

关键配置在 `src/config.py`，可通过环境变量覆盖：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| RAG_ENGINE | langchain | RAG引擎：langchain / original / agentic |
| AGENT_WEB_SEARCH | true | Agentic 引擎是否启用联网搜索 |
| TAVILY_API_KEY | - | Agentic 联网搜索密钥（https://tavily.com） |
| AGENT_CRITIC_MAX_RETRY | 3 | Agentic Critic 最大反思重试次数 |
| LLM_PROVIDER | deepseek | LLM提供商：deepseek/openai/anthropic |
| OPENAI_API_KEY | - | OpenAI兼容API密钥（DeepSeek等） |
| OPENAI_BASE_URL | https://api.deepseek.com | API地址 |
| OPENAI_MODEL | deepseek-chat | 模型名 |
| CHUNK_SIZE | 800 | 切片大小 |
| CHUNK_OVERLAP | 100 | 切片重叠 |
| RETRIEVAL_TOP_K | 10 | 检索返回数量 |
| USE_HYBRID_RETRIEVAL | true | 启用混合检索 |
| LLM_TEMPERATURE | 0.7 | LLM 温度 |
| MAX_HISTORY_ROUNDS | 5 | 多轮对话保留轮数 |

## API 接口

### 认证

```bash
# 注册
curl -X POST http://localhost:8080/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "yourname", "password": "yourpass"}'

# 登录
curl -X POST http://localhost:8080/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "yourname", "password": "yourpass"}'
```

### 问答

```bash
curl -X POST http://localhost:8080/api/v1/query \
  -H "Authorization: Bearer <your_token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "什么是RAG", "top_k": 5}'
```

### 上传文档

```bash
curl -X POST http://localhost:8080/api/v1/documents/upload \
  -H "Authorization: Bearer <your_token>" \
  -F "file=@your_doc.pdf" -F "kb_id=default"
```

## 安全说明

系统内置多层安全防护：

1. **Prompt 注入检测** — 自动拦截恶意指令（如"忽略之前的指令"）
2. **文件预检查** — 验证文件魔数签名，防止伪装文件
3. **内容防泄露** — 拦截批量导出查询，保护知识库数据
4. **输出清洗** — 过滤 LLM 输出中的敏感内容

## 更新日志

- [v1.3.0 — Agentic RAG（M10 升级）](docs/superpowers/specs/2026-08-23-agentic-rag-design.md)
- [v1.2.0 — LangChain框架集成](docs/v1.2.0-changelog.md)
- [v1.1.0 — 管理后台 + 安全功能](docs/v1.1.0-changelog.md)

## License

MIT
