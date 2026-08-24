# QA Agent

一个面向中文问答场景的全栈 Agent 项目。后端基于 FastAPI、LangGraph、DeepSeek、PostgreSQL 与 pgvector，前端基于 React、TypeScript 和 Vite；当前已包含账号体系、流式问答、RAG、本地 tracing、指标监控，以及 Token、缓存命中与推理过程展示。

> 本文优先解决“如何在本地从零跑通项目”。推荐开发方式是：数据库和后端运行在 Docker 中，前端运行在本机。

## 系统结构

```text
Browser :3002
    │
    │ /api（Vite 代理）
    ▼
FastAPI :8001 ── LangGraph ── DeepSeek API
    │              │
    │              ├── RAG 检索工具
    │              └── 本地 JSONL tracing
    ▼
PostgreSQL + pgvector :5433
    ▲
    └── SiliconFlow BGE-M3 Embedding
```

## 已有能力

- DeepSeek `deepseek-v4-flash` 模型调用与流式输出
- LangGraph 状态图、工具调用和 PostgreSQL checkpoint
- 基于 pgvector 与 `BAAI/bge-m3` 的文档导入和语义检索
- JWT 登录、注册、会话与消息历史
- React + TypeScript 聊天页面
- 实时 Token、缓存命中率、reasoning effort 与推理内容面板
- 仅记录性能、调用链、Token 和 Error 的本地 tracing
- structlog、Prometheus、Grafana 与容器指标

## 一、准备环境

建议安装以下工具：

| 工具 | 用途 | 建议版本 |
| --- | --- | --- |
| Git | 获取与管理代码 | 当前稳定版 |
| Docker Desktop | 启动 PostgreSQL、后端和监控服务 | 当前稳定版，使用 Linux containers |
| Python | 本机执行导入、检查和测试 | 3.13 |
| [uv](https://docs.astral.sh/uv/) | Python 依赖管理 | 当前稳定版 |
| Node.js | 运行前端 | 22 LTS |
| pnpm | 前端依赖管理 | 11.19.0 |
| GNU Make | 执行 Makefile 快捷命令 | 可选；Windows 可直接使用下文的 PowerShell 命令 |

至少还需要准备两个 API Key：

1. `DEEPSEEK_API_KEY`：负责问答、会话命名和长期记忆抽取。
2. `SILICONFLOW_API_KEY`：负责 RAG 和长期记忆的向量嵌入。

## 二、创建开发配置

在项目根目录复制环境变量模板：

```powershell
Copy-Item .env.example .env.development
```

macOS/Linux 可执行：

```bash
cp .env.example .env.development
```

编辑 `.env.development`，开发环境至少确认下面这些值：

```dotenv
APP_ENV=development
PROJECT_NAME="QA Agent"
DEBUG=true

# 宿主机访问后端的端口
APP_PUBLISHED_PORT=8001
ALLOWED_ORIGINS="http://localhost:3002,http://localhost:8001"

# DeepSeek 官方 API
DEEPSEEK_API_KEY="替换为你的 DeepSeek API Key"
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEFAULT_LLM_MODEL=deepseek-v4-flash
DEEPSEEK_THINKING_ENABLED=false

# 仅建议在本地开发时打开；生产环境应为 false
EXPOSE_REASONING_CONTENT=true

# SiliconFlow Embedding
SILICONFLOW_API_KEY="替换为你的 SiliconFlow API Key"
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIM=1024

# JWT：请替换为随机长字符串
JWT_SECRET_KEY="替换为至少 32 字节的随机密钥"

# 容器内连接数据库必须使用 db:5432
POSTGRES_HOST=db
POSTGRES_PORT=5432

# 宿主机通过 5433 访问容器数据库
POSTGRES_PUBLISHED_PORT=5433
POSTGRES_DB=mydb
POSTGRES_USER=myuser
POSTGRES_PASSWORD="替换为强密码"

GRAFANA_ADMIN_PASSWORD="替换为强密码"
```

PowerShell 可以用下面的命令生成 JWT 密钥：

```powershell
$bytes = New-Object byte[] 48
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
[Convert]::ToBase64String($bytes)
```

`.env.development` 已被 Git 忽略。不要把真实 API Key、数据库密码或 JWT 密钥写入 `.env.example` 或提交到仓库。

### 关于两个数据库端口

- `POSTGRES_PORT=5432` 是 Docker 网络内部端口，后端容器使用它连接 `db`。
- `POSTGRES_PUBLISHED_PORT=5433` 是 Windows/宿主机访问数据库的端口。
- 如果 `5433` 被占用，可以换成其他空闲端口；容器内的 `POSTGRES_PORT` 不需要跟着改。

## 三、启动数据库和后端

### 方式 A：PowerShell / 不依赖 Make

```powershell
docker compose --env-file .env.development up -d --build db app
docker compose --env-file .env.development exec -T app /app/.venv/bin/alembic upgrade head
docker compose --env-file .env.development ps
```

### 方式 B：Make

```bash
make docker-up ENV=development
make docker-migrate ENV=development
```

确认后端健康：

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

也可以直接打开：

- API 健康检查：<http://localhost:8001/health>
- Swagger API 文档：<http://localhost:8001/docs>

健康接口返回 `status: healthy`，并且 `database` 为 `healthy`，才表示数据库与后端已经连通。

## 四、启动 React 前端

打开一个新的终端：

```powershell
Set-Location frontend
pnpm install --frozen-lockfile
pnpm dev
```

Windows 也可以使用项目提供的启动脚本：

```powershell
Set-Location frontend
pnpm install --frozen-lockfile
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

浏览器访问 <http://localhost:3002>。

前端源码必须由 Vite 开发服务器加载，不能直接双击 `frontend/index.html`。Vite 会把 `/api` 请求代理到 `http://127.0.0.1:8001`。

## 五、导入文档并跑通 RAG

导入脚本目前支持 `.md`、`.txt` 和 `.rst`。先在项目根目录安装本机 Python 依赖：

```powershell
uv sync --all-extras --all-groups
```

由于 `.env.development` 中的 `db:5432` 只在 Docker 网络内可用，从宿主机执行导入脚本时需要临时覆盖数据库地址：

```powershell
$env:APP_ENV = "development"
$env:POSTGRES_HOST = "127.0.0.1"
$env:POSTGRES_PORT = "5433"
uv run python scripts/ingest_docs.py ".\你的文档目录"
Remove-Item Env:POSTGRES_HOST
Remove-Item Env:POSTGRES_PORT
Remove-Item Env:APP_ENV
```

如果你修改了 `POSTGRES_PUBLISHED_PORT`，这里的 `POSTGRES_PORT` 也要改成相同的宿主机端口。

常用导入命令：

```powershell
# 导入一个目录
uv run python scripts/ingest_docs.py ".\knowledge"

# 导入单个文件
uv run python scripts/ingest_docs.py ".\knowledge\guide.md"

# 清空知识库后重新导入
uv run python scripts/ingest_docs.py ".\knowledge" --reset
```

重新导入同一个来源时会原子替换旧切片；如果新的 Embedding 请求失败，旧的可检索版本会被保留。

## 六、完整验证流程

建议按下面的顺序验证，而不是只看首页能否打开：

1. 访问 <http://localhost:8001/health>，确认 API 和数据库都是健康状态。
2. 访问 <http://localhost:3002>，注册一个测试账号。
3. 密码至少 8 位，并同时包含大写字母、小写字母、数字和特殊字符。
4. 登录后创建会话，发送一条普通问题，确认能够收到流式回复。
5. 导入一份内容明确的测试文档，再询问只有该文档能回答的问题。
6. 查看前端检查面板中的 Token、缓存、调用阶段和推理信息。
7. 查看 `logs/`，确认应用日志和 trace 文件已生成。

如果上述七步都通过，说明账号、前后端连接、数据库、模型调用、SSE 流式传输和 RAG 主链路已经跑通。

## 可选：启动完整监控栈

```powershell
docker compose --env-file .env.development up -d
```

默认入口：

- Prometheus：<http://localhost:9090>
- Grafana：<http://localhost:3000>
- cAdvisor：<http://localhost:8080>

这些端口都只绑定到本机。Grafana 登录密码来自 `GRAFANA_ADMIN_PASSWORD`。

## 日志与 tracing

项目使用 structlog 写应用日志，并使用自研的内容无关 tracing layer 记录：

- 性能与耗时
- Agent、Tool、LLM、RAG、Memory、Streaming 调用链
- Token 使用量
- Error 类型与状态

trace 不记录 prompt、回答、工具参数、检索文档、密钥或原始推理内容。

默认文件位置：

```text
logs/development-YYYY-MM-DD.jsonl
logs/traces/trace-YYYY-MM-DD-PID.jsonl
```

查看某一次调用链：

```powershell
uv run python scripts/trace_view.py <trace_id>
```

更完整的说明见 [docs/observability.md](docs/observability.md)。

## 常用命令

### Docker

```powershell
# 查看状态
docker compose --env-file .env.development ps

# 查看后端和数据库日志
docker compose --env-file .env.development logs -f app db

# 执行数据库迁移
docker compose --env-file .env.development exec -T app /app/.venv/bin/alembic upgrade head

# 停止当前项目服务
docker compose --env-file .env.development down
```

### 后端检查

```powershell
uv run ruff check .
uv run ruff format --check .
uv run python -m pyright
uv run pytest -m "not integration" -q
```

### 前端检查

```powershell
Set-Location frontend
pnpm typecheck
pnpm build
```

## 常见问题

### 直接打开 `index.html` 是空白页面

这是 Vite 项目，不是一个可直接打开的静态 HTML 文件。运行 `pnpm dev`，然后访问 <http://localhost:3002>。

### 注册失败

先检查：

1. <http://localhost:8001/health> 是否健康。
2. 是否执行过 Alembic migration。
3. 密码是否同时包含大小写字母、数字和特殊字符。
4. `docker compose --env-file .env.development logs app` 中是否有数据库或配置错误。

### 后端显示数据库连接失败或无法解析 `db`

`db` 只存在于 Docker Compose 网络。后端在容器内运行时使用 `db:5432`；本机脚本使用 `127.0.0.1:5433`，或使用你在 `POSTGRES_PUBLISHED_PORT` 中设置的端口。

### `8001`、`3002` 或 `5433` 已被占用

- 后端：修改 `.env.development` 中的 `APP_PUBLISHED_PORT`，并同步修改 `frontend/vite.config.ts` 的代理目标。
- 前端：修改 `frontend/vite.config.ts` 的 `server.port`，并同步修改 `ALLOWED_ORIGINS`。
- PostgreSQL：只修改 `POSTGRES_PUBLISHED_PORT`；本机导入脚本使用同一个新端口。

### 模型调用失败

检查 `DEEPSEEK_API_KEY`、账户余额、网络连接和 `DEEPSEEK_BASE_URL`。启动日志不会打印完整密钥。

### RAG 没有召回内容

确认已经执行文档导入、`SILICONFLOW_API_KEY` 有效、Embedding 模型仍为 1024 维的 `BAAI/bge-m3`，并检查导入命令是否连接到了当前 Docker 数据库。

## 项目结构

```text
app/
  api/v1/              FastAPI 路由、认证和聊天接口
  core/langgraph/      Agent 图、节点与工具
  core/tracing.py      本地 tracing 实现
  models/              SQLModel 数据模型
  schemas/             Pydantic 请求、响应与图状态
  services/            LLM、数据库、RAG、缓存和记忆服务
frontend/              React + TypeScript + Vite 前端
alembic/               数据库迁移
evals/                 LLM 评测框架
scripts/               文档导入、trace 查看与运维脚本
tests/                 后端测试
docs/                  架构与可观测性文档
```

## 上线前注意事项

当前版本适合本地开发、演示和继续完善。公开部署前仍应至少完成：

- HTTPS、反向代理与安全响应头的生产配置
- 使用 HttpOnly Cookie 或同等级方案保护浏览器令牌
- 私有知识库的用户/租户 ACL 隔离
- 生产级密钥管理、数据库备份和恢复演练
- 关闭 `DEBUG` 和 `EXPOSE_REASONING_CONTENT`
- 根据实际容量完成压力测试、限流与连接池调优

## License

MIT
