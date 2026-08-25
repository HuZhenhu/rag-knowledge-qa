# MCP 与 API 开发知识总结

## 一、MCP (Model Context Protocol) 是什么

MCP 是一个插件系统，让 Claude 能调用外部服务的能力。

**工作流程：**
```
Claude Code  ←—stdio/JSON-RPC—→  MCP Server  ←—→  外部服务/API/数据库
```

MCP 服务器可以暴露三种能力：
- **Tools** — 可调用的函数（最常用）
- **Resources** — 可读取的数据源
- **Prompts** — 预定义的提示模板

---

## 二、MCP 配置文件结构

```jsonc
{
  "mcpServers": {
    "server-name": {
      "command": "npx",           // 启动命令（npx、node、python 都行）
      "args": ["-y", "包名"],      // 命令参数
      "env": {                     // 环境变量（传密钥等）
        "API_KEY": "your-secret-key"
      }
    }
  }
}
```

配置文件位置：`~/.claude/settings.json` 或项目级 `.claude/settings.json`

---

## 三、开发 MCP 服务器

### TypeScript 示例

```bash
mkdir my-mcp-server && cd my-mcp-server
npm init -y
npm install @modelcontextprotocol/sdk zod
```

```typescript
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const server = new McpServer({
  name: "my-custom-server",
  version: "1.0.0",
});

server.tool(
  "get_weather",
  "获取指定城市的天气信息",
  { city: z.string().describe("城市名") },
  async ({ city }) => {
    const data = await fetch(`https://api.example.com/weather?city=${city}`);
    const json = await data.json();
    return {
      content: [{ type: "text", text: JSON.stringify(json) }],
    };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
```

### Python 示例

```bash
pip install mcp
```

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-python-server")

@mcp.tool()
async def get_weather(city: str) -> str:
    """获取指定城市的天气信息"""
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://api.example.com/weather?city={city}")
        return resp.text

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### 调试

```bash
# TypeScript
npx @modelcontextprotocol/inspector node index.js

# Python
mcp dev server.py
```

### 开发建议

| 要点 | 说明 |
|------|------|
| Tool 描述要写好 | Claude 靠描述决定何时调用 |
| 用 zod/Pydantic 做参数校验 | 防止传入错误数据 |
| 错误要返回文本，不要抛异常 | 优雅处理错误 |
| stdio 是唯一传输方式 | Claude Code 只支持 stdio |

---

## 四、API 是什么

**一句话：API 就是别人写好的"接口"，你调用它就能拿到数据或完成操作。**

**生活类比：** 你去餐厅吃饭，你（程序）→ 服务员（API）→ 厨房（服务器/数据库）

你不需要知道厨房怎么炒菜，你只需要跟服务员说"来一份宫保鸡丁"，服务员把菜端给你。

---

## 五、开发中常用的 API

### 日常工具类

| API | 用途 | 典型场景 |
|-----|------|---------|
| 天气 | 查天气、预报 | 智能助手、出行 App |
| 翻译 | 文本翻译 | 国际化、内容处理 |
| 地图/定位 | 地理编码、路线规划 | 外卖、打车、物流 |
| 短信/邮件 | 发验证码、通知 | 注册登录、营销 |
| OCR | 图片识别文字 | 拍照录入、发票识别 |

### AI/大模型类

| API | 用途 |
|-----|------|
| Claude API | 对话、分析、代码生成 |
| OpenAI API | GPT 系列模型 |
| 通义千问/文心一言 | 国内大模型 |
| Stable Diffusion / DALL-E | AI 生图 |
| Whisper | 语音转文字 |

### 内容/数据类

| API | 用途 |
|-----|------|
| GitHub API | 操作仓库、Issue、PR |
| 新闻 API | 抓取新闻资讯 |
| 股票/金融 API | 行情数据、K 线 |
| 电商 API | 商品查询、订单管理（淘宝、京东） |
| 快递 API | 物流追踪（快递100） |

### 开发基础设施类

| API | 用途 |
|-----|------|
| 云服务 | AWS / 阿里云 / 腾讯云（服务器、存储、CDN） |
| 数据库 | MySQL / Redis / MongoDB 的客户端 API |
| 对象存储 | 阿里 OSS / 腾讯 COS / AWS S3（存图片、文件） |
| 支付 | 微信支付 / 支付宝（收款、退款） |
| 身份认证 | OAuth2、微信登录、Google 登录 |

### 社交/通讯类

| API | 用途 |
|-----|------|
| 微信公众号/小程序 | 推送消息、获取用户信息 |
| 钉钉/飞书 | 机器人通知、审批流 |
| Slack/Discord | 消息推送、Bot |
| 邮件 | SendGrid / SMTP 发邮件 |

---

## 六、MCP 与 API 的关系

MCP 服务器做的事情就是：**把 API 包装成 Claude 能调用的工具。**

```
用户说："查一下北京天气"
  ↓
Claude 看到你注册了 get_weather 工具
  ↓
Claude 调用你的 MCP 服务器
  ↓
你的代码里调用天气 API
  ↓
结果返回给 Claude，Claude 告诉用户
```

---

## 七、在哪里找 API

- **聚合平台**：RapidAPI、API Hub — 海量 API 集市
- **官方文档**：GitHub API、微信支付 API 等都有详细文档
- **国内平台**：聚合数据、阿里云 API 市场、百度 API Store

---

*总结于 2026-05-11*
