# 企业 AI 中台总体架构

## 1. 已确认目标

- 服务约 200 名企业员工，一期主场景为“企业知识库 + 文档生产”。
- 当前部署在私人 AWS 账号，产品完成后迁移到企业自有服务器。
- 允许通过统一模型网关调用 OpenAI、Anthropic Claude、Google Gemini；业务代码不得直接依赖供应商 SDK。
- 企业微信尚未授权，一期使用可替换的临时身份提供方。
- 一期提供受控的 Agent 可视化编排，支持固定节点、版本、运行状态、人工审核和 Trace；不提供任意代码执行。
- 前端采用 Stitch 暗色液态玻璃视觉语言，但可访问性与性能优先于装饰效果。

## 2. 运行拓扑

```text
Browser / WeCom
      │
      ▼
Web (React + Vite) ── typed runtime transport
      │
      ▼
API / Control Plane (FastAPI)
├── Identity & Authorization
├── Knowledge/Asset/Workflow CRUD
├── Document Task API
├── Approval, Audit, Cost Ledger
└── SSE Run Events
      │                 │
      │ execution      │ model request
      ▼                ▼
Workflow Runtime    Model Gateway
├── InProcessGraph  ├── OpenAI Responses adapter
├── event log/SSE   ├── Anthropic Messages adapter
├── human pause     └── Gemini generateContent adapter
└── LangGraph port       │
      │                    ▼
      ├── In-memory repositories/index (current)
      ├── PostgreSQL + pgvector / Redis / MQ (planned)
      └── ObjectStorage (memory/S3 current → enterprise compatible)
```

一期验收默认启用进程内确定性执行器、词法知识索引与确定性 Markdown Composer，因此不需要外部 API Key。设置显式逻辑模型路由和对应凭证后，可将文档 Composer 切到三家外部 Provider；没有自动 fallback。`LangGraphExecutor` 当前是适配边界而不是已启用依赖；二期需要跨进程 checkpoint、长任务恢复和分布式 Worker 时，再由该边界接入 LangGraph 与队列。Alembic 初始迁移和租户隔离 Repository 已提供，但当前 Container 仍使用内存实现；切换 PostgreSQL 前还必须完成全部运行态仓储接管与重启恢复测试。

## 3. 不可跨越的边界

1. 浏览器只访问平台 API，不接触供应商 Key、企业微信 Token、对象存储凭证或 Worker。
2. API 是身份、租户、权限、预算、资产状态和最终审批的权威来源。
3. Worker 只消费带 `permissionSnapshot`、定义版本和幂等键的任务；执行检索、工具或下载前仍须服务端重新授权。
4. Model Gateway 统一解析 `logicalModelCode`，记录实际供应商、模型、Token、价格版本和 Trace；业务数据不依赖当前路由反推历史。
5. 数据库保存业务状态和资产元数据；对象存储保存原文件与产物；Trace 系统不能替代正式审计账本。
6. 可视化 Workflow 只接受白名单节点和声明式 JSON 配置。条件表达式使用受限 JSON Logic，不执行用户 JavaScript、Python 或 Shell。

## 4. 契约优先

`packages/contracts/` 是跨端边界：

- JSON Schema：跨 Python/TypeScript 的运行时校验权威来源；
- OpenAPI 3.1：HTTP、上传、异步任务与 SSE 入口；
- TypeScript 类型：Web 与 Node 工具消费；
- Examples：联调与契约测试夹具。

契约字段只允许向后兼容增加；破坏性变更必须提升主版本、提供迁移期并同时更新 Schema、类型、OpenAPI、示例与契约测试。

## 5. 一期安全默认值

下列内容是一期发布门禁；当前离线闭环已实现写请求幂等、租户隔离、开发身份 ACL、服务端权限快照、引用溯源和对象键隐藏，其余项目仍需在生产部署前完成。

- 所有写请求必须支持 `Idempotency-Key`。
- 上传文件先做类型、大小、病毒与内容扫描，再进入解析队列。
- 权限过滤必须在召回之前执行，不能检索后再遮盖。
- 正式发布必须人工审核；模型不得直接改变正式业务状态。
- 外部模型发送前按数据等级和用户授权执行策略检查；默认不发送 `confidential` 数据。
- 日志和 Trace 不保存 Key、会话 Token、原始敏感文档全文。
- 模型调用有部门预算、单次上限、超时、重试上限和紧急停止开关。
