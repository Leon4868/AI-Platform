# Enterprise AI Platform

面向企业员工的 AI 中台，第一阶段聚焦 **企业知识库、文档生产与可视化 Agent 编排**，并把生成结果、来源引用、审批记录、版本和运行轨迹持续沉淀为企业资产。

> 当前仓库是全新重建的 MVP 基线。默认使用开发身份、内存仓储和确定性运行节点，不填写外部模型凭证也能启动和验收界面、工作流与 API。HTTP 失败不会静默降级为 Mock；界面 Mock 必须显式启用并带有标识。

## 产品边界

- 员工侧：知识检索、引用溯源、文档生成、Agent 工作流运行与人工审核。
- 管理侧：知识库、模型路由、工作流版本、资产血缘、权限与审计。
- 部署侧：当前可落在私人 AWS；通过 PostgreSQL、S3 兼容存储和身份 Provider 适配层迁移到企业服务器。
- 安全侧：前端不持有模型密钥；检索、工具调用与资产下载均由服务端重新鉴权。

## 架构

```text
员工 / 管理员
      │
      ▼
React 工作台 ── 共享 Contracts ── FastAPI
  Agent Canvas                    ├─ Identity Provider（开发 / 企业微信）
  Knowledge & Docs                ├─ Knowledge / Document / Asset
  Trace & Approval                ├─ Workflow Runtime / Audit
                                  ├─ Model Gateway（Mock / 外部模型）
                                  ├─ PostgreSQL + pgvector
                                  └─ S3 Compatible（AWS S3 / MinIO）
```

当前启用的是 FastAPI 进程内的 `InProcessGraphExecutor`：它执行受白名单约束的有向无环图，支持人工暂停、取消、append-only 事件日志与 SSE 断点续传。代码已预留 `LangGraphExecutor` 适配边界，但尚未安装或启用 LangGraph；二期需要持久化 checkpoint、分布式 Worker 和长任务恢复时再接入。

### 当前可验收闭环

- 员工工作台：64px 图标导航可切换 Agent、知识库、文档生产和企业资产；节点库与配置面板使用可收起抽屉，聚焦和运行态共用跑马灯组件。
- 知识库：上传 UTF-8 `txt` / `md` / `html`，离线解析、规范化、分块和词法检索；Citation 返回文档、Chunk、Asset、原文片段与分数，并支持文档、资产、范围、密级和标题过滤。PDF、未知二进制及异常文本会明确标记 `failed`，不会生成伪引用。
- 文档生产：`Document Task → Workflow Run → Markdown Draft Asset` 已贯通；任务、运行、Trace、引用、内容 SHA-256 和 `DERIVED_FROM` 血缘可关联。
- Agent 编排：定义保存、图校验、启动、人工暂停、取消、SSE 事件、断点续传和权限快照已落地；Web 默认连接真实 API，Mock 必须显式开启。
- 模型网关：OpenAI Responses、Anthropic Messages 与 Gemini `generateContent` Provider 已接入显式版本路由；缺失路由或凭证时直接失败且不发起网络请求，不做隐式供应商降级。默认 Composer 仍是可离线验收的确定性实现。
- 安全基线：租户隔离、开发身份下的部门/项目/密级 ACL、写操作幂等及异载荷冲突、个人草稿、内部对象键隐藏和短时下载 URL 已覆盖自动化测试。

### 尚未生产化

当前 Repository、知识索引、幂等记录、审计和任务跟随器仍在单进程内存中，进程重启或多实例部署会丢失状态；Alembic 初始迁移和租户隔离的 SQLAlchemy Repository 已就绪，但尚未接管运行时。检索尚未接入 pgvector、Embedding、Rerank、DOCX、OCR 或 PDF 解析；文档产物目前只有 Markdown。外部 Provider 已可按配置调用，但预算账本、生产重试策略、内容外发审批和持久 Trace 仍需完成。

| 目录 | 职责 |
| --- | --- |
| `apps/web` | React + TypeScript + Vite + Tailwind CSS v4 的企业工作台与 Agent 编排画布 |
| `apps/api` | FastAPI 业务 API、权限、审计、工作流、数据与供应商适配层 |
| `packages/contracts` | OpenAPI、JSON Schema、跨端 TypeScript 类型与脱敏示例 |
| `docs/architecture` | 模块边界、迁移策略、分期路线与协作规范 |

## 快速启动

需要 Node.js 22+、pnpm 11+、Python 3.12+ 与 uv。

```bash
cp .env.example .env
make bootstrap
```

分别启动前后端：

```bash
make dev-api
make dev-web
```

- Web：`http://localhost:5173`
- API：`http://localhost:8000`
- OpenAPI：`http://localhost:8000/docs`

## 验证

```bash
./scripts/verify.sh all
make build
```

前端验收重点：64px 图标导航、覆盖式左右抽屉、13 节点工作流、Trace 状态条，以及输入框聚焦 `2.8s` / 运行 `1.2s` 的同轨跑马灯。

运行态通过 Fetch `ReadableStream` 消费 SSE，使用 `Last-Event-ID` 恢复、按 `runId + sequence` 去重、检测序号缺口，并在成功、失败或取消终态主动关闭连接。

验收命令覆盖 Contracts/OpenAPI、Web、API、Integration、Runtime internal 与 Persistence；以当前命令输出为准，不在文档中固化易过期的测试数量。

## 分期路线

1. 一期：知识库、文档生产、开发身份、可视化编排、人工审核、审计与资产归档。
2. 二期：企业微信 SSO、增量同步、混合检索/重排、审批通知、模型配额与质量评测。
3. 三期：多模态资产、工具/MCP 治理、跨部门 Agent 模板市场、私有模型与多集群部署。

详细方案见 [`docs/architecture`](docs/architecture)。

## 开发约束

- 同类能力出现两次即抽离为组件、Hook、协议、Schema 或 Provider。
- 业务层只依赖逻辑模型名、对象键和身份接口，不依赖某一家云或模型厂商。
- 真实 API Key、企业 Token、证书和用户原文不得提交到仓库或示例文件。
- 工作流与资产采用版本化契约；运行时保存权限快照、Trace 和来源血缘。
