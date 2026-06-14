# Plan: RAG 管理后台 + Chunk 复制功能

## 需求概述

将当前单页聊天应用改造为后台管理系统，包含仪表盘、文档管理（含 chunk 查看/复制）、索引监控、查询日志、用户管理。保留原有聊天功能作为独立页面。
## 前端页面效果图
 参考docs里面的admin-dashboard-demo.html，换成中文的
## 现有基础

| 已有 | 缺少 |
|------|------|
| 30+ 个后端 API | 前端无 router |
| JWT 认证 + 3级角色 (viewer/editor/admin) | 无后台布局组件 |
| WebSocket 数据监控 (/ws/data-monitor) | 无 HTTP 客户端（全靠 WS） |
| Element Plus UI 库 | 无状态管理（Pinia） |
| @element-plus/icons-vue 已装未用 | 图标未注册 |
| 文档注册表 (document_registry) | 无获取单文档 chunk 列表的 API |

## 文件变更清单

### 新增文件

| 文件 | 用途 |
|------|------|
| `frontend/src/router/index.ts` | 路由配置 + 认证守卫 |
| `frontend/src/stores/auth.ts` | 登录状态 + token 管理 (Pinia) |
| `frontend/src/stores/theme.ts` | 深色/浅色主题切换 (Pinia) |
| `frontend/src/stores/locale.ts` | 中英文语言切换 (Pinia) |
| `frontend/src/i18n/zh.ts` | 中文语言包 |
| `frontend/src/i18n/en.ts` | 英文语言包 |
| `frontend/src/i18n/index.ts` | i18n 配置 |
| `frontend/src/api/client.ts` | axios 实例 + JWT 拦截器 |
| `frontend/src/layouts/AdminLayout.vue` | 后台布局（侧栏+顶栏+内容区） |
| `frontend/src/views/Login.vue` | 登录页 |
| `frontend/src/views/Dashboard.vue` | 仪表盘 |
| `frontend/src/views/Documents.vue` | 文档管理 + chunk 查看复制 |
| `frontend/src/views/IndexMonitor.vue` | 索引监控（实时进度） |
| `frontend/src/views/QueryLogs.vue` | 查询日志 |
| `frontend/src/views/Users.vue` | 用户管理 |
| `frontend/src/views/Chat.vue` | 原有聊天功能迁移 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `frontend/package.json` | 添加 vue-router, pinia, axios |
| `frontend/src/main.ts` | 注册 router + pinia + icons |
| `frontend/src/App.vue` | 改为 `<router-view>` |
| `frontend/src/types/index.ts` | 新增 admin 相关类型 |
| `src/api/routes.py` | 新增 GET /documents/{id}/chunks 接口 |
| `src/api/schemas.py` | 新增 ChunkInfo, ChunkListResponse 模型 |
| `src/storage/database.py` | 新增 get_user_list() 等 admin 查询 |

### 删除文件

| 文件 | 原因 |
|------|------|
| `frontend/src/components/HelloWorld.vue` | Vite 脚手架死代码 |

---

## 实现阶段

### Phase 1: 基础设施

**1.1 安装依赖**
```bash
cd frontend
npm install vue-router@4 pinia axios vue-i18n@9
```

**1.2 HTTP 客户端 `api/client.ts`**
- axios 实例，baseURL 从 env 读取（默认 `/api/v1`）
- 请求拦截器：自动附加 `Authorization: Bearer <token>`
- 响应拦截器：401 → 清 token → 跳转 /login
- 导出 `authClient`（带 token）和 `publicClient`（无 token，用于登录）

**1.3 认证状态 `stores/auth.ts`**
- Pinia store：`token`, `user` (id, username, role)
- `login(username, password)` → 调用 `/auth/login` → 存 token 到 localStorage
- `logout()` → 清 token → 跳转 /login
- `isLoggedIn` getter
- 刷新 token：`refreshToken()` 调用 `/auth/refresh`

**1.4 路由 `router/index.ts`**
```
/login        → Login.vue        (无需认证)
/             → AdminLayout
  /dashboard  → Dashboard.vue    (admin)
  /documents  → Documents.vue    (viewer+)
  /index      → IndexMonitor.vue (admin)
  /logs       → QueryLogs.vue    (admin)
  /users      → Users.vue        (admin)
  /chat       → Chat.vue         (viewer+)
```
- 路由守卫：未登录 → /login；权限不足 → /dashboard

**1.5 登录页 `views/Login.vue`**
- Element Plus `el-form` 表单
- 调用 `authStore.login()`
- 登录成功 → 跳转 /dashboard

**1.6 更新 `main.ts`**
- 注册 createRouter, createPinia
- 全局注册 `@element-plus/icons-vue` 图标组件

**1.7 主题系统 `stores/theme.ts`**
- Pinia store：`theme`（'dark' | 'light'）
- `toggleTheme()` → 切换主题 → 存 localStorage
- 通过 CSS 变量实现，参考 `docs/admin-dashboard-demo.html` 的配色方案
- 所有颜色用 `var(--xxx)` 引用，切换时只改变 `data-theme` 属性
- Element Plus 深色模式：`<html class="dark">` + `element-plus/theme-chalk/dark/css-vars.css`

**1.8 国际化 `stores/locale.ts` + `i18n/`**
- 使用 `vue-i18n@9`
- 语言包：`zh.ts`（中文）、`en.ts`（英文）
- 切换按钮放在顶栏（和主题切换按钮并排）
- 存 localStorage，刷新后保持
- 覆盖范围：侧栏菜单、页面标题、表格列名、按钮文案、提示信息
- 后端返回的数据（文档名、chunk 内容）不做翻译

**1.9 主题 + 语言切换按钮位置**
```
顶栏右侧：[中/EN] [🌙/☀️] [用户名] [退出]
```
- 语言切换：点击 "中" 变 "EN"，再点变回 "中"
- 主题切换：月亮/太阳图标
- 两个按钮紧挨着，用 `el-button-group` 或 flex 布局

---

### Phase 2: 后台布局 + 仪表盘

**2.1 管理后台布局 `layouts/AdminLayout.vue`**
```
┌──────────────────────────────────────────────────┐
│  顶部: Logo + [中/EN] [🌙/☀️] + 用户名 + 退出    │
├──────┬───────────────────────────────────────────┤
│ 侧栏 │                                           │
│      │       内容区 <router-view>                │
│ 仪表盘│                                           │
│ 文档  │                                           │
│ 索引  │                                           │
│ 日志  │                                           │
│ 用户  │                                           │
│ 聊天  │                                           │
└──────┴───────────────────────────────────────────┘
```
- `el-container` + `el-aside` + `el-main`
- 侧栏用 `el-menu`，图标用 `@element-plus/icons-vue`
- 顶部 `el-header`：左侧 Logo，右侧 [语言切换] [主题切换] [用户名] [退出]
- 主题切换时整个布局平滑过渡（CSS transition 0.3s）

**2.2 仪表盘 `views/Dashboard.vue`**
- 调用 `/health` → 向量库状态卡片
- 调用 `/stats` → 文档数、chunk 数
- 调用 `/metrics` → 查询数、错误率、延迟统计
- 调用 `/alerts` → 最近告警列表
- 用 `el-row` + `el-col` + `el-card` 网格布局

---

### Phase 3: 文档管理 + Chunk 复制

**3.1 后端：新增 chunks 接口**

`src/api/schemas.py` 新增：
```python
class ChunkInfo(BaseModel):
    chunk_id: str
    content: str
    section: str
    page_number: int | None
    content_type: str
    score: float | None

class ChunkListResponse(BaseModel):
    document_id: str
    filename: str
    chunks: list[ChunkInfo]
    total: int
```

`src/api/routes.py` 新增：
```python
@router.get("/documents/{document_id}/chunks", response_model=ChunkListResponse)
async def get_document_chunks(document_id: str, user = Depends(get_current_user)):
    """获取文档的所有 chunk 列表"""
    # 1. 从 document_registry 获取 filename
    # 2. 从 vector_store 按 source_file 过滤所有 chunk
    # 3. 返回 ChunkListResponse
```

`src/core/vector_store.py` 新增：
```python
def query_by_source(self, source_file: str, limit: int = 1000) -> dict:
    """按源文件名查询所有 chunk"""
    # 用 collection.get(where={"source_file": source_file})
```

**3.2 前端：文档管理页 `views/Documents.vue`**

表格列：文件名 | 类型 | Chunk数 | 索引时间 | 状态 | 操作

操作列：
- "查看Chunks" 按钮 → 展开行 / 打开 Drawer
- "删除" 按钮（二次确认）

**3.3 Chunk 查看/复制 Drawer**

点击"查看Chunks"打开 `el-drawer`：
```
┌─────────────────────────────────────┐
│ 文档名: 治安管理处罚法.pdf           │
│ 共 46 个 chunks                     │
├─────────────────────────────────────┤
│ #1  第4页  [文本]        [📋 复制]  │
│ ┌─────────────────────────────────┐ │
│ │ 第十条 治安管理处罚的种类为：   │ │
│ │ （一）警告；                     │ │
│ │ （二）罚款；                     │ │
│ ...                               │ │
│ └─────────────────────────────────┘ │
│                                     │
│ #2  第5页  [表格]        [📋 复制]  │
│ ┌─────────────────────────────────┐ │
│ │ ...                             │ │
│ └─────────────────────────────────┘ │
│                                     │
│ (滚动加载更多)                      │
└─────────────────────────────────────┘
```

- 每个 chunk 用 `el-card` 展示
- 头部：序号 + 页码 + 类型标签
- 内容：`<pre>` 或 `<div>` 展示原始文本
- 右上角复制按钮：`navigator.clipboard.writeText(chunk.content)` + `ElMessage.success("已复制")`
- 支持"复制全部 chunks"按钮

---

### Phase 4: 索引监控 + 查询日志

**4.1 索引监控 `views/IndexMonitor.vue`**
- watcher 状态：调用 `/index/watcher/status`，显示运行/停止 + 启停按钮
- 实时进度：从 `useDataMonitor` composable 获取 `indexProgress`
- 进度条：`el-progress` 显示 current/total
- 索引完成通知：显示最近一次索引的 stats

**4.2 查询日志 `views/QueryLogs.vue`**
- 调用 `/traces` 列表
- 调用 `/audit_logs?action=query` 查询审计日志
- `el-table` 展示：时间 | 用户 | 查询内容 | 耗时 | 状态
- 调用 `/metrics` 展示统计图表（用 `el-statistic`）

---

### Phase 5: 用户管理 + 聊天迁移

**5.1 用户管理 `views/Users.vue`**

后端新增接口：
```python
@router.get("/users", response_model=list[dict])
async def list_users(user = Depends(require_role("admin"))):
    """管理员查看用户列表"""
```

前端：
- `el-table` 展示：用户名 | 角色 | 状态 | 最后登录 | 操作
- 操作：启用/禁用（调用 admin API）

**5.2 聊天页 `views/Chat.vue`**
- 把现有 App.vue 的聊天逻辑迁移过来
- 保留 WebSocket 连接、消息展示、输入框
- 布局适配 AdminLayout 的内容区

**5.3 清理**
- `App.vue` 简化为 `<router-view>`
- 删除 `HelloWorld.vue`

---

## 验证步骤

```bash
# 1. 前端编译
cd frontend && npm run build    # TypeScript 无错误

# 2. 前端测试
npm run test                     # 现有测试通过

# 3. 后端测试
cd .. && python -m pytest tests/ -x -q  # 390+ 测试通过

# 4. 手动验证
#    a. 访问 localhost:5173 → 跳转登录页
#    b. admin 登录 → 进入仪表盘
#    c. 侧栏切换各页面
#    d. 文档页 → 点击"查看Chunks" → 复制单个 chunk
#    e. 放新文件到 data/ → 索引监控页实时显示进度
#    f. 聊天页正常问答
```

## 风险

| 风险 | 可能性 | 缓解 |
|------|--------|------|
| 后端缺 `/users` 列表接口 | 中 | Phase 5 补一个简单接口 |
| WebSocket URL 硬编码 | 低 | 改为从 env 读取 |
| 大文档 chunk 数量过多导致 Drawer 卡顿 | 低 | 分页加载，每页 50 条 |
| 现有测试因 import 变更失败 | 低 | 聊天逻辑不变，只移动文件 |

## 复杂度：Medium

| 阶段 | 预估 |
|------|------|
| Phase 1: 基础设施 | ~2h |
| Phase 2: 布局 + 仪表盘 | ~2h |
| Phase 3: 文档 + Chunk 复制 | ~2h |
| Phase 4: 索引监控 + 日志 | ~1.5h |
| Phase 5: 用户管理 + 迁移 | ~1.5h |
| **总计** | **~9h** |

---

## 评审意见（2026-06-12）

### 整体评价

计划完整度高，5 个 Phase 从基础设施到功能页面逐步推进，顺序正确。风险识别到位。

### 建议调整

**0. UI 风格参考**

已制作 HTML 原型：`docs/admin-dashboard-demo.html`
- Ethereal Glass 深色科技风 + 浅色模式
- CSS 变量驱动，一键切换
- 实际开发时用 Element Plus 组件库实现，保持同样的配色和圆角风格

**1. Phase 3 和 Phase 4 合并**

文档管理和索引监控都是数据运维相关，合并为一个 Phase 可以减少上下文切换，预估时间不变（~3.5h）。

**2. `/users` 接口不需要新建**

当前已有 `POST /auth/register`（注册）和 `POST /auth/login`（登录）。只需新增一个 `GET /users` 接口让 admin 查看用户列表即可，工作量很小，不需要单独一个 Phase。

**3. Chunk 复制增加批量导出**

除了单个复制按钮，建议加一个"复制全部为 Markdown"按钮，把该文档的所有 chunk 拼接成一个 Markdown 文本，方便批量导出。

**4. WebSocket 和 HTTP API 不要混用**

当前聊天用 WebSocket，管理页面用 HTTP API。两套通信方式不要混在一个页面里。管理页面统一用 axios + HTTP API，聊天页保留 WebSocket。

**5. 最大风险：前端迁移**

当前 `App.vue` 是单文件聊天应用，改成 router + layout + 多页面后，聊天逻辑要迁移到 `Chat.vue`。这个过程容易引入 bug。

**缓解方案：** Phase 1 完成后，先把聊天功能原封不动迁到 `Chat.vue`，验证能跑通，再做其他页面。不要边迁移边加功能。

### 修订后的 Phase 建议

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase 1 | 基础设施（router + auth + axios + 主题 + 国际化 + 聊天迁移） | ~3h |
| Phase 2 | 布局 + 仪表盘 | ~2h |
| Phase 3 | 文档管理 + Chunk 复制 + 索引监控 + 查询日志 | ~3h |
| Phase 4 | 用户管理 + 清理 | ~1.5h |
| **总计** | | **~9.5h** |
