# 多模型适配系统实现计划

## 概述

当前系统硬编码了 DeepSeek/OpenAI/Anthropic 三种 LLM 和本地 sentence-transformers Embedding，切换模型需要改代码和环境变量。需要设计一个统一的模型接口层，通过适配器模式支持所有主流大模型厂商，并提供前端配置向导和自动路由能力。

## 现状分析

**当前问题:**
- `src/core/generator.py` 直接用 `if LLM_PROVIDER == "anthropic"` 分支调用不同 SDK，新增厂商需改源码
- `src/core/embedder.py` 只支持本地 sentence-transformers，无 API Embedding 适配
- `src/core/langchain_rag.py` 硬编码 `ChatOpenAI`，无法切换到其他 LLM
- `src/config.py` 所有配置通过 `.env` 静态读取，运行时无法热切换
- 前端无模型配置界面，用户必须手动编辑 `.env`

## 架构设计

```
┌─────────────────────────────────────────────────┐
│                 前端模型配置向导                    │
│  (ModelWizard.vue - 选厂商 → 填Key → 测试 → 保存)  │
└──────────────────────┬──────────────────────────┘
                       │ HTTP API
┌──────────────────────▼──────────────────────────┐
│              src/api/model_routes.py              │
│  GET/POST /api/v1/models/config                  │
│  POST     /api/v1/models/test                    │
│  GET      /api/v1/models/registry                │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│           src/core/models/manager.py              │
│         ModelManager (单例，热切换)                │
│  - 当前活跃 LLM/Embedding 实例                    │
│  - switch_llm() / switch_embedding()             │
│  - 自动路由: route_by_content()                   │
└──────┬──────────────────┬───────────────────────┘
       │                  │
┌──────▼──────┐    ┌──────▼──────┐
│ LLM 适配器   │    │Embedding适配│
│ (BaseLLM)   │    │(BaseEmbed)  │
├─────────────┤    ├─────────────┤
│ OpenAI      │    │ Local(ST)   │
│ DeepSeek    │    │ OpenAI      │
│ Claude      │    │ DeepSeek    │
│ Gemini      │    │ Gemini      │
│ Qwen(通义)  │    │ Qwen(通义)  │
│ Ollama      │    │ Jina        │
│ Moonshot    │    │ Cohere      │
└─────────────┘    └─────────────┘
```

**核心设计原则:**
1. 适配器模式: 每个厂商一个适配器类，实现统一接口
2. 策略模式: ModelManager 持有当前活跃适配器，可运行时切换
3. 配置持久化: SQLite 存储模型配置，不再仅依赖 `.env`
4. 向后兼容: 现有 `.env` 配置作为默认值，数据库配置优先

## 文件结构

**新增文件:**
```
src/core/models/
├── __init__.py
├── base.py              # BaseLLM + BaseEmbedding 抽象基类
├── registry.py          # 模型注册表（厂商元数据、能力声明）
├── manager.py           # ModelManager 单例（热切换 + 自动路由）
├── router.py            # 自动路由逻辑（按内容类型选模型）
├── llm/
│   ├── __init__.py
│   ├── openai_llm.py    # OpenAI 适配器
│   ├── deepseek_llm.py  # DeepSeek 适配器（继承OpenAI，覆盖base_url）
│   ├── claude_llm.py    # Anthropic Claude 适配器
│   ├── gemini_llm.py    # Google Gemini 适配器
│   ├── qwen_llm.py      # 阿里通义千问适配器
│   ├── ollama_llm.py    # 本地 Ollama 适配器
│   └── moonshot_llm.py  # Moonshot/Kimi 适配器
├── embedding/
│   ├── __init__.py
│   ├── local_embed.py   # 本地 sentence-transformers
│   ├── openai_embed.py  # OpenAI Embedding API
│   ├── deepseek_embed.py
│   ├── gemini_embed.py
│   ├── qwen_embed.py
│   └── jina_embed.py    # Jina Embedding API

src/api/
└── model_routes.py      # 模型配置 API 路由

frontend/src/
├── views/ModelConfig.vue         # 模型配置主页面
├── components/ModelWizard.vue    # 配置向导组件
├── components/ModelTestPanel.vue # 连通性测试面板
├── components/ModelStatus.vue    # 模型状态卡片
└── stores/model.ts               # 模型配置 Pinia store
```

**修改文件:**
```
src/config.py              # 新增模型配置常量
src/core/generator.py      # 重构为使用 ModelManager
src/core/embedder.py       # 重构为使用 ModelManager
src/core/langchain_rag.py  # LLM/Embedding 从 ModelManager 获取
src/storage/database.py    # 新增 model_configs 表
src/storage/models.py      # 新增 ModelConfig 数据类
src/api/routes.py          # 注册 model_routes
main.py                    # 初始化 ModelManager
frontend/src/router/index.ts      # 新增 /models 路由
frontend/src/layouts/AdminLayout.vue  # 侧栏新增"模型配置"菜单
frontend/src/i18n/zh.ts + en.ts   # 新增模型配置相关翻译
```

## 实现步骤

### Phase 1: 抽象基类 + 注册表 (基础层)

**1. 创建 BaseLLM 抽象基类** (File: `src/core/models/base.py`)
- Action: 定义 `BaseLLM` ABC，包含 `chat()`, `chat_stream()`, `get_model_info()` 方法；定义 `BaseEmbedding` ABC，包含 `embed()`, `embed_single()`, `get_dimension()` 方法
- Why: 所有适配器的统一契约，替换当前 generator.py 的 if-else 分支
- Dependencies: 无
- Risk: Low
- 关键接口:
  ```python
  class BaseLLM(ABC):
      @abstractmethod
      def chat(self, messages: list[dict], temperature: float, max_tokens: int) -> dict: ...
      @abstractmethod
      def chat_stream(self, messages: list[dict], temperature: float, max_tokens: int) -> Iterator[str]: ...
      def get_model_info(self) -> dict: ...  # 返回 {provider, model, capabilities}

  class BaseEmbedding(ABC):
      @abstractmethod
      def embed(self, texts: list[str]) -> list[list[float]]: ...
      @abstractmethod
      def embed_single(self, text: str) -> list[float]: ...
      @abstractmethod
      def get_dimension(self) -> int: ...
  ```

**2. 创建模型注册表** (File: `src/core/models/registry.py`)
- Action: 定义 `ModelRegistry` 类，注册所有厂商的元数据（名称、支持的能力、默认配置、所需的 API Key 字段）
- Why: 前端配置向导需要知道有哪些厂商可选、每个厂商需要填什么
- Dependencies: Step 1
- Risk: Low

**3. 创建目录结构** (File: `src/core/models/__init__.py`, `src/core/models/llm/__init__.py`, `src/core/models/embedding/__init__.py`)
- Action: 创建包目录，导出核心类
- Dependencies: 无
- Risk: Low

### Phase 2: LLM 适配器实现

**4. OpenAI 适配器** (File: `src/core/models/llm/openai_llm.py`)
- Action: 实现 `OpenAILLM(BaseLLM)`，使用 `openai` SDK
- Why: 最基础的适配器，DeepSeek/Moonshot/Ollama 都继承它
- Dependencies: Step 1
- Risk: Low

**5. DeepSeek 适配器** (File: `src/core/models/llm/deepseek_llm.py`)
- Action: 继承 `OpenAILLM`，覆盖 `base_url` 和默认模型名
- Why: DeepSeek 兼容 OpenAI 格式，只需覆盖少量配置
- Dependencies: Step 4
- Risk: Low

**6. Claude 适配器** (File: `src/core/models/llm/claude_llm.py`)
- Action: 实现 `ClaudeLLM(BaseLLM)`，使用 `anthropic` SDK，处理 Claude 特有的消息格式（system 独立传入）
- Why: Claude API 与 OpenAI 差异较大，需要独立实现
- Dependencies: Step 1
- Risk: Low

**7. Gemini 适配器** (File: `src/core/models/llm/gemini_llm.py`)
- Action: 实现 `GeminiLLM(BaseLLM)`，使用 `google-genai` SDK
- Why: Google 的 API 格式与 OpenAI 不同
- Dependencies: Step 1
- Risk: Medium -- Gemini API 格式变化较快

**8. 通义千问适配器** (File: `src/core/models/llm/qwen_llm.py`)
- Action: 继承 `OpenAILLM`，覆盖 `base_url` 为 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- Why: 通义千问兼容 OpenAI 格式
- Dependencies: Step 4
- Risk: Low

**9. Ollama 适配器** (File: `src/core/models/llm/ollama_llm.py`)
- Action: 继承 `OpenAILLM`，默认 `base_url=http://localhost:11434/v1`，添加模型列表发现功能
- Why: 本地模型部署，无需 API Key
- Dependencies: Step 4
- Risk: Low

**10. Moonshot 适配器** (File: `src/core/models/llm/moonshot_llm.py`)
- Action: 继承 `OpenAILLM`，覆盖 `base_url`
- Dependencies: Step 4
- Risk: Low

### Phase 3: Embedding 适配器实现

**11. 本地 Embedding 适配器** (File: `src/core/models/embedding/local_embed.py`)
- Action: 将现有 `src/core/embedder.py` 的逻辑包装为 `LocalEmbedding(BaseEmbedding)`
- Why: 保持向后兼容
- Dependencies: Step 1
- Risk: Low

**12. OpenAI Embedding 适配器** (File: `src/core/models/embedding/openai_embed.py`)
- Action: 实现 `OpenAIEmbedding(BaseEmbedding)`，调用 `text-embedding-3-small/large`
- Dependencies: Step 1
- Risk: Low

**13. 其他 Embedding 适配器** (Files: `deepseek_embed.py`, `gemini_embed.py`, `qwen_embed.py`, `jina_embed.py`)
- Action: 分别实现各厂商的 Embedding 适配器
- Why: 覆盖主流 Embedding 服务
- Dependencies: Step 1
- Risk: Low

### Phase 4: ModelManager + 热切换 + 自动路由

**14. ModelManager 核心** (File: `src/core/models/manager.py`)
- Action: 实现 `ModelManager` 单例类：
  - `get_llm() -> BaseLLM` / `get_embedding() -> BaseEmbedding`: 获取当前活跃实例
  - `switch_llm(provider, model, config)`: 运行时切换 LLM
  - `switch_embedding(provider, model, config)`: 运行时切换 Embedding
  - `test_connection(provider, config) -> dict`: 测试连通性
  - `_load_from_db()`: 启动时从 SQLite 加载配置
  - `_save_to_db()`: 保存配置到 SQLite
  - 回退逻辑: 数据库无配置时读 `.env`，`.env` 无配置时用注册表默认值
- Why: 整个系统的核心调度器
- Dependencies: Steps 1-13
- Risk: High -- 需要处理并发安全、实例缓存、错误回退

**15. 自动路由** (File: `src/core/models/router.py`)
- Action: 实现 `ModelRouter` 类：
  - 短文本查询 → 轻量模型（如 DeepSeek-Chat）
  - 长文档摘要 → 大上下文模型（如 Claude/Gemini）
  - 代码相关 → 代码模型（如 DeepSeek-Coder）
  - 多模态图片 → 支持 vision 的模型
  - Embedding → 根据语言选择（中文用 BGE，英文用 OpenAI）
  - 路由规则可配置，存数据库
- Why: 智能选择最优模型，降低成本
- Dependencies: Step 14
- Risk: Medium -- 路由规则需要调优

**16. SQLite 模型配置表** (File: `src/storage/database.py`)
- Action: 新增 `model_configs` 表:
  ```sql
  CREATE TABLE IF NOT EXISTS model_configs (
      id TEXT PRIMARY KEY,
      config_type TEXT NOT NULL,      -- 'llm' / 'embedding'
      provider TEXT NOT NULL,         -- 'openai' / 'deepseek' / ...
      model_name TEXT NOT NULL,
      api_key TEXT DEFAULT '',
      base_url TEXT DEFAULT '',
      extra_config TEXT DEFAULT '{}', -- JSON: temperature, max_tokens 等
      is_active INTEGER DEFAULT 0,
      is_default INTEGER DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
  )
  ```
- Why: 持久化模型配置，支持热切换
- Dependencies: 无
- Risk: Low

**17. ModelConfig 数据类** (File: `src/storage/models.py`)
- Action: 新增 `ModelConfig` dataclass
- Dependencies: Step 16
- Risk: Low

### Phase 5: 重构现有代码使用 ModelManager

**18. 重构 Generator** (File: `src/core/generator.py`)
- Action: 删除现有的 `_init_client()` / `_call_openai()` / `_call_anthropic()` 方法，改为从 `ModelManager.get_llm()` 获取适配器实例调用
- Why: 消除 if-else 分支，统一走适配器
- Dependencies: Step 14
- Risk: High -- 核心路径，需确保流式/非流式都正常
- 关键变更:
  ```python
  class Generator:
      def __init__(self):
          self._manager = ModelManager()

      def generate(self, question, sources, history=None, summary=""):
          llm = self._manager.get_llm()
          messages = self._build_messages(question, sources, history, summary)
          return llm.chat(messages, temperature=LLM_TEMPERATURE, max_tokens=LLM_MAX_TOKENS)
  ```

**19. 重构 Embedder** (File: `src/core/embedder.py`)
- Action: 改为从 `ModelManager.get_embedding()` 获取适配器
- Dependencies: Step 14
- Risk: Medium

**20. 重构 LangChain RAG** (File: `src/core/langchain_rag.py`)
- Action: `ChatOpenAI` 和 `HuggingFaceEmbeddings` 改为从 ModelManager 获取配置动态创建
- Dependencies: Step 14
- Risk: Medium -- LangChain 的 LLM 初始化方式需要适配

**21. 注册新路由** (File: `src/api/routes.py`)
- Action: `from src.api.model_routes import model_router; router.include_router(model_router)`
- Dependencies: Step 22
- Risk: Low

### Phase 6: 模型配置 API

**22. 模型配置 API 路由** (File: `src/api/model_routes.py`)
- Action: 实现以下接口:
  - `GET /api/v1/models/registry` -- 返回所有支持的厂商和模型列表
  - `GET /api/v1/models/config` -- 返回当前模型配置
  - `POST /api/v1/models/config` -- 保存模型配置（含 API Key 加密存储）
  - `POST /api/v1/models/test` -- 测试模型连通性（发送测试请求）
  - `POST /api/v1/models/switch` -- 热切换当前活跃模型
  - `GET /api/v1/models/status` -- 返回当前模型状态（provider, model, latency, last_error）
- Why: 前端配置向导的后端支撑
- Dependencies: Step 14, 16
- Risk: Medium -- API Key 需要加密存储

**23. Pydantic Schema** (File: `src/api/schemas.py`)
- Action: 新增 `ModelConfigRequest`, `ModelConfigResponse`, `ModelTestResponse`, `ModelRegistryResponse` 等 Schema
- Dependencies: 无
- Risk: Low

### Phase 7: 前端模型配置界面

**24. 模型配置 Pinia Store** (File: `frontend/src/stores/model.ts`)
- Action: 实现 `useModelStore`：
  - `fetchRegistry()` -- 获取厂商列表
  - `fetchConfig()` -- 获取当前配置
  - `saveConfig()` -- 保存配置
  - `testModel()` -- 测试连通性
  - `switchModel()` -- 热切换
- Dependencies: Step 22
- Risk: Low

**25. 模型配置主页面** (File: `frontend/src/views/ModelConfig.vue`)
- Action: 实现配置页面，包含:
  - 当前活跃模型状态卡片（provider, model, 延迟, 上次调用时间）
  - LLM 配置区（厂商下拉 → 模型下拉 → API Key 输入 → Base URL 输入）
  - Embedding 配置区（同上）
  - 自动路由规则配置（可选）
  - 保存按钮
- Why: 用户配置模型的主入口
- Dependencies: Step 24
- Risk: Low

**26. 配置向导组件** (File: `frontend/src/components/ModelWizard.vue`)
- Action: 实现分步向导:
  - Step 1: 选择用途（LLM / Embedding / 两者都要）
  - Step 2: 选择厂商（卡片式，带 logo 和简介）
  - Step 3: 填写配置（API Key, Model, Base URL -- 根据厂商动态表单）
  - Step 4: 测试连通性（发送测试请求，显示延迟和响应预览）
  - Step 5: 确认保存
- Why: 降低配置门槛，引导用户完成首次配置
- Dependencies: Step 25
- Risk: Low

**27. 路由和菜单注册** (Files: `frontend/src/router/index.ts`, `frontend/src/layouts/AdminLayout.vue`, `frontend/src/i18n/zh.ts`, `frontend/src/i18n/en.ts`)
- Action: 新增 `/models` 路由，侧栏菜单新增"模型配置"项，添加 i18n 翻译
- Dependencies: Step 25
- Risk: Low

### Phase 8: 依赖更新 + 集成测试

**28. 更新 requirements.txt** (File: `requirements.txt`)
- Action: 新增可选依赖:
  - `google-genai` (Gemini)
  - `ollama` (Ollama，可选，也可用 openai SDK 兼容)
  - 注: `openai` 和 `anthropic` 已有
- Why: 新适配器需要对应 SDK
- Dependencies: Steps 4-13
- Risk: Low

**29. 更新 .env.example** (File: `.env.example`)
- Action: 新增所有厂商的环境变量模板，加注释说明
- Dependencies: 无
- Risk: Low

**30. 集成测试** (File: `tests/test_model_adapters.py`)
- Action: 测试每个适配器的:
  - 初始化（正确参数 / 缺少 API Key）
  - chat() 调用（mock SDK 响应）
  - chat_stream() 流式调用
  - 错误处理（网络超时、API 错误、无效 Key）
  - ModelManager 热切换
- Dependencies: Steps 1-14
- Risk: Low

## 测试策略

- **单元测试**: 每个适配器独立测试（mock SDK），覆盖正常/异常路径
- **集成测试**: ModelManager 热切换流程，配置保存/加载/回退
- **前端测试**: ModelWizard 步骤流程，API 调用 mock
- **手动测试**: 真实 API Key 连通性测试（DeepSeek, OpenAI, Claude）

## 风险与缓解

- **风险**: API Key 安全存储
  - 缓解: 数据库中 AES 加密存储，API 返回时脱敏显示（只显示前4后4位）
- **风险**: 热切换时正在进行的请求
  - 缓解: 切换不影响已创建的 client 实例，新请求使用新实例
- **风险**: 不同厂商的 token 计费方式不同
  - 缓解: 统一 usage 字段格式，各适配器负责转换
- **风险**: Gemini SDK 国内访问需要代理
  - 缓解: base_url 可配置，文档中说明代理设置

## 成功标准

- [ ] 新增一个 LLM 厂商只需创建一个文件（~50行），不改任何现有代码
- [ ] 前端配置向导能在 3 步内完成模型切换
- [ ] 热切换后 1 秒内新请求使用新模型
- [ ] 所有现有测试不受影响（向后兼容）
- [ ] 自动路由能根据查询内容选择合适的模型
