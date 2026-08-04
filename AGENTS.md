@SPEC.md
# RAG智能问答系统 — Codex 项目规范

## 项目概述
企业级RAG智能问答助手。用户提问后，系统从知识库检索相关文档片段，基于检索结果生成带引用标注的回答。
支持多格式文档、查询理解、回答可追溯、开放API。

## 技术栈
- **语言**: Python 3.12+
- **包管理**: uv（WSL里没有pip）
- **LLM**: DeepSeek API（兼容OpenAI格式，base_url=https://api.deepseek.com）
- **Embedding**: sentence-transformers all-MiniLM-L6-v2（本地模型）/ DeepSeek API（可切换）
- **向量数据库**: ChromaDB（本地持久化）
- **关键词检索**: rank_bm25
- **ReRanker**: sentence-transformers CrossEncoder
- **文档处理**: LangChain、python-docx、pdfplumber
- **Web前端**: Vue 3 + Vite + TypeScript + Element Plus
- **API服务**: FastAPI + uvicorn + WebSocket
- **数据库**: SQLite（API Key、会话、日志）

## 项目结构
```
rag-knowledge-qa/
├── src/
│   ├── api/               # FastAPI接口层
│   │   ├── routes.py      # 所有API路由
│   │   ├── auth.py        # API Key鉴权
│   │   ├── rate_limit.py  # 限流中间件
│   │   └── schemas.py     # Pydantic请求/响应模型
│   ├── core/              # RAG核心逻辑
│   │   ├── loaders/       # 文档加载器（插件化）
│   │   ├── splitter.py    # 智能切片
│   │   ├── embedder.py    # Embedding（本地/API可切换）
│   │   ├── vector_store.py # ChromaDB封装
│   │   ├── retriever.py   # 检索（向量+BM25混合）
│   │   ├── reranker.py    # ReRanker重排序
│   │   ├── query_understander.py # 查询理解
│   │   ├── generator.py   # LLM生成（带引用标注）
│   │   ├── rag_engine.py  # RAG引擎（串联所有模块）
│   │   ├── session.py     # 多轮对话
│   │   └── preprocessor.py # 表格/图片预处理
│   ├── storage/           # 持久化层
│   │   ├── database.py    # SQLite
│   │   └── models.py      # 数据表定义
│   └── config.py          # 配置管理
├── data/                  # 知识库文件（不修改）
├── chroma_db/             # ChromaDB持久化（git忽略）
├── images/                # 图片存储
├── evaluation/            # 评测集
├── frontend/              # Vue 3 前端
│   ├── src/
│   │   ├── components/    # Vue组件
│   │   ├── composables/   # 组合式函数
│   │   ├── stores/        # Pinia状态
│   │   ├── types/         # TypeScript类型
│   │   └── styles/        # 样式
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
├── app.py                 # Gradio前端（调试用，可选）
├── main.py                # FastAPI启动入口
├── build_index.py         # 构建向量索引
├── manage_keys.py         # API Key管理
├── evaluate.py            # 评测脚本
├── SPEC.md                # 项目规格说明书
└── .env                   # API密钥（不入git）
```

## 编码规范

### Python 风格
- **Python 版本**: 3.12+
- **类型注解**: 所有公共函数必须标注参数和返回值类型
- **命名**: 类名 PascalCase，函数/变量 snake_case，常量 UPPER_SNAKE
- **导入顺序**: 标准库 → 第三方 → 项目内部，每组空一行
- **文档字符串**: 每个类和公共方法写三引号 docstring
- **行宽**: 不超过 100 字符
- **禁止**: `from module import *`，魔法数字（用常量）

### 代码质量工具
- **Linter/Formatter**: Ruff
- **类型检查**: MyPy（初期宽松）

### Git 提交规范
- **格式**: `<type>(<scope>): <description>`
- **类型**: feat / fix / docs / style / refactor / test / chore
- **示例**: `feat(core): 添加混合检索模块`

## 命令
```bash
# 环境（用uv不用pip）
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 构建索引
python build_index.py

# 启动Gradio前端（调试用）
python app.py

# 启动API服务
python main.py
# 或
uvicorn src.web.server:app --host 0.0.0.0 --port 8080

# 前端开发
cd frontend
npm install
npm run dev              # 本地开发 http://localhost:5173

# 前端生产构建
npm run build            # 输出到 frontend/dist/

# 代码检查
ruff check src/ tests/
```

## 开发原则

1. **先跑通再优化**: Phase 1 先用最简单的方式跑通完整RAG链路，后续Phase再加查询理解、混合检索等高级功能
2. **每个模块独立可测**: 每个模块（loader/splitter/embedder/retriever/generator）都能单独import和测试
3. **配置驱动**: 所有参数（chunk_size、top_k、模型名等）从config.py读取，不硬编码
4. **错误不能炸**: LLM调用失败、检索无结果等异常情况必须优雅处理，不能让服务崩溃
5. **中文优先**: 知识库是中文的，Embedding模型和prompt模板都要考虑中文效果
6. **插件化loader**: 新增文档格式只需写一个新的loader类，不改核心代码

## 当前阶段

Phase 1 — 核心RAG链路：加载→切片→Embedding→检索→LLM生成→带引用的回答
按 SPEC.md 中的 Phase 顺序实现。先跑通最小可用版本。

## 关键设计决策

### 回答可追溯（核心设计目标）
- LLM生成时，prompt强制要求标注引用[1][2][3]
- 不确定时必须说"知识库中未找到相关信息"，不能编造
- 前端点击引用可展开原文chunk

### 查询理解
- 查询扩展：LLM把问题拆成多个子查询分别检索
- 多路检索：向量检索 + BM25关键词检索，RRF融合
- HyDE：用假设性回答去检索，缩小语义鸿沟
- ReRanker：CrossEncoder精排

### 切片策略
- Markdown：按# ## ###标题切分，超长的再按字数兜底
- 表格：整体保留不切分，同时生成自然语言描述
- 图片：OCR或多模态大模型生成描述文本

### Embedding可切换
- 默认：sentence-transformers本地模型（免费）
- 可选：DeepSeek Embedding API（中文效果更好）
- 通过config中EMBEDDING_PROVIDER切换，接口统一

## 数据目录
data/ 下的文件是知识库数据源，**不要修改**。
当前有23个md文件，后续会增加docx/pdf/图片。
