# ADR 0005：前端视觉美学方向 — 知识工坊 / 技术编辑部

- 日期：2026-08-26
- 状态：已接受
- 影响范围：`frontend/`（Vue 3 + Vite + TS + Element Plus）
- 关联任务：《frontend-role-ui-redesign-task.md》B 部分

## 背景

B 部分要求去除当前"紫色渐变 + 白卡片 + 系统字体"的通用 AI 风格，从三个候选美学方向（知识工坊暖纸页 / 精密仪表深色数据面板 / 编辑杂志风）中三选一并贯彻到所有页面，不得混合，不得回到紫色渐变。

## 决策

选定 **方案 1：知识工坊 / 技术编辑部**。

理由：
1. 产品是**知识库/文档问答平台**，暖纸质地与深墨排版最贴合"知识、文档、内容"的气质，也区别于市面通用的 AI 聊天紫。
2. 在亮/暗双主题下都能通过"纸感底色 + 暖色强调"保持一致性格，暗色退化为"暖墨"而非纯黑，辨识度高。
3. 表格页（文档/用户/日志/评测）与聊天页（产品核心）在同一方向下都自然协调，便于全局统一。

## 色板

| 用途 | 亮色（纸感） | 暗色（暖墨） |
|------|--------------|--------------|
| 页面底色 `--bg` | `#F4EEDF` | `#191511` |
| 卡片底色 `--bg-surface` | `#FBF7EC` | `#221C15` |
| 深墨文字 `--text-1` | `#292217` | `#EDE2CB` |
| 次要文字 `--text-2` | `#61563F` | `#B5A68A` |
| 弱化文字 `--text-3` | `#9C8E72` | `#7A6E58` |
| 边框 `--border` | `#E2D6BC` | `#3A3223` |
| **主导色**（琥珀/赭石）`--accent` | `#B45309` | `#E6A94F` |
| **锐利点缀色**（深墨绿）`--accent-2` | `#2F5D50` | `#7FBFA6` |
| 成功 `--success` | `#3E7A4C` | `#6AA97E` |
| 危险 `--error` | `#B3392E` | `#D06A5E` |
| 警告 `--warning` | `#A9761B` | `#D9A54A` |

只用 1 个主导色（琥珀/赭石）+ 1 个锐利点缀色（深墨绿，用于引用/批注/链接类高亮）；语义色保持可区分。

## 字体

- 中文：**思源黑体 Noto Sans SC（Variable，@fontsource-variable/noto-sans-sc）**，失败回退 `PingFang SC / Source Han Sans SC / Microsoft YaHei`（仅 fallback，不作主字体）。
- 西文/数字：**JetBrains Mono（@fontsource/jetbrains-mono，400/500/700）**，用于数字、代码、等宽点缀（统计值、时间戳、事件计数、路径、logo 字元）。
- 明确排除 Inter/Space Grotesk 等被用滥的字体。

## 其它设计令牌（B2）

- 圆角：`--radius-sm .5rem` / `--radius .875rem` / `--radius-lg 1.375rem` / `--radius-full`。
- 阴影：`--shadow-sm/md/lg` 三档暖调阴影。
- 间距：`--space-1..4` 四档。
- 动效：CSS 过渡为主；登录页 staggered reveal、聊天流式光标（caret-blink）、卡片 rise-in 入场；严格遵守 `prefers-reduced-motion`。
- Element Plus：通过 `--el-*` 变量映射到本令牌（`--el-color-primary` 等），不重写组件。

## 实施位置

- 设计令牌集中定义于 `frontend/src/style.css`（亮/暗双主题 + Element Plus 变量映射 + 全局过渡与减弱动效），`main.ts` 导入。
- 各页面仅消费 CSS 变量，无硬编码紫色。
