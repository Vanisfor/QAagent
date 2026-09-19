# QA Agent Frontend

React + TypeScript developer workspace for the FastAPI/LangGraph backend.

```bash
pnpm install
pnpm dev
```

The Vite development server runs on `http://localhost:3002` and proxies `/api`
to `http://localhost:8001`.

The UI uses a dark-first, documentation-inspired shell with semantic CSS design
tokens, desktop and mobile navigation, a real command menu, session history,
POST/SSE chat streaming, runtime telemetry, BYOK model settings, and locally
persisted appearance preferences. Motion for React is limited to layout and
state transitions; streamed tokens are never animated individually, and user
reduced-motion preferences are respected.

## 路由与交互

使用 React Router 的 BrowserRouter，Node.js 需要 22.22.0 或更新版本。

| URL | 行为 |
| --- | --- |
| `/` | 跳转到 `/chat` |
| `/login` | 登录或注册；登录后返回原先请求的聊天地址 |
| `/chat` | 恢复当前账户上次选择的会话，否则打开最近会话 |
| `/chat/:sessionId` | 打开指定会话，支持刷新、收藏和浏览器前进后退 |

会话地址只会从当前账户返回的会话列表中解析；不存在或不可访问的会话显示恢复入口。后端仍负责身份验证和访问控制。

个性化设置、头像、账户信息、模型/API 配置和背景设置继续使用 Modal，头像菜单使用 Popover，不改变 URL。打开设置保留聊天草稿和正在生成的回答；切换会话或退出登录会中止当前浏览器中的流式请求。刷新后从后端加载历史，不恢复未发送的草稿或流式连接。

### 验证

在 `frontend` 目录执行：

```sh
pnpm typecheck
pnpm build
pnpm test:e2e
```

浏览器测试使用模拟 API 和流式响应，无需真实账户或后端。Windows 默认使用已安装的 Microsoft Edge；其他平台先执行 `pnpm exec playwright install chromium`。测试会自动启动端口 3015 的 Vite 服务。

### 部署

Vite 开发服务与 `pnpm preview` 支持 SPA 回退。部署 `dist/` 时，静态服务器也需要将 `/chat/*` 等前端地址回退到 `index.html`，否则直接访问或刷新会返回 404。例如 Nginx 在保留单独 `/api/` 反向代理配置的情况下使用：

```nginx
location / {
    try_files $uri $uri/ /index.html;
}
```

开发模式下 `/api` 仍由 Vite 代理至 `http://127.0.0.1:8001`。生产 API 代理需单独配置，不能回退到前端 HTML。
