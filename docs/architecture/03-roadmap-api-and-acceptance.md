# 分期、API 契约与验收基线

## 1. 一期：知识库与文档生产 MVP

目标：30–50 人试点，架构容量支持 200 人。

目标交付：

- 临时账号登录和统一 Identity Provider 接口；
- 文件上传、解析、切分、索引、权限前置过滤、混合检索与引用；
- 文档总结、改写、翻译、模板生成，草稿保存、人工审核与发布；
- Workflow 可视化画布，支持当前契约中的固定节点、版本、校验、运行、失败重试、人工暂停与 SSE 进度；
- OpenAI/Anthropic/Google Provider Adapter，业务仅使用逻辑模型名；
- 文档资产的版本、血缘、审计、费用和 Trace；其他资产类型先具备统一元数据模型；
- 私人 AWS Docker 部署、备份与一键迁移所需适配层；
- Stitch 暗色液态玻璃工作台、知识库、文档任务、Workflow 编辑器和资产详情页。

### 当前离线切片状态

已完成：员工侧 Agent/知识库/文档/资产工作台；开发身份与权限快照；`txt/md/html` 离线解析、分块、词法检索、过滤和 Citation；定义校验、运行、人工暂停、取消与 SSE；Document Task 驱动真实 Workflow Run 并产出个人 Markdown Draft Asset；租户/部门/项目/密级 ACL；写操作幂等；资产哈希、血缘与可过期的短时下载 URL；OpenAI/Anthropic/Gemini 显式路由 Provider；Alembic 启动迁移；核心业务实体、Run/Event、词法知识索引与审计的可切换 PostgreSQL Repository。

未完成：企业微信、幂等结果与 checkpoint 持久化、pgvector、Redis/MQ 和中断任务续跑、混合检索/Rerank、PDF/DOCX/OCR、预算持久账本、内容外发审批、正式审批发布、生产 Trace 查询面，以及完整 AWS/企业服务器部署演练。因此本节的“目标交付”不能视为当前全部可用能力。

## 2. 二期：200 人与多部门治理

- 企业微信登录、组织同步、离职禁用与资产交接；
- 部门/项目空间、RBAC + 数据范围、预算、灰度发布；
- SharePoint/Confluence/网盘等连接器（由数据源确认后排期）；
- 图片、音频、视频资产的上传、审核、检索和血缘；
- Tool/MCP Gateway、双重授权、写操作审批；
- 3–5 个生产 Agent、评测门禁、Prompt/Workflow 版本回滚；
- 混合检索、Rerank、增量同步和 RAG 质量评测。

## 3. 三期：企业生产化

- 迁移企业服务器，Kubernetes、高可用、灾备与容量压测；
- 完整多模态生成、DLP、内容安全、红队与代码执行沙箱；
- 部门自助 Agent/Workflow 模板市场与受控多 Agent 协作；
- A/B 实验、离线/在线评测、资产推荐和 ROI 看板；
- ABAC、临时授权、权限定期复核与合规报表。

## 4. API 契约

权威文件为 [`packages/contracts/openapi.yaml`](../../packages/contracts/openapi.yaml)。一期最小接口：

| 方法 | 路径 | 用途 | 关键门禁 |
|---|---|---|---|
| GET/POST | `/api/v1/knowledge-bases` | 列表/创建知识库 | 数据范围、部门管理员 |
| POST | `/api/v1/knowledge-bases/{id}/documents` | 上传并异步索引 | 文件扫描、幂等 |
| POST | `/api/v1/knowledge-bases/{id}/search` | 权限过滤检索 | 检索前授权、引用 |
| POST | `/api/v1/workflow-definitions` | 保存不可变定义版本 | 图校验、节点白名单 |
| POST | `/api/v1/workflows/{workflow_id}/runs` | 启动运行 | 定义版本、权限快照、幂等 |
| GET | `/api/v1/workflow-runs/{run_id}` | 查询运行快照 | 租户隔离、Trace |
| POST | `/api/v1/workflow-runs/{run_id}/cancel` | 幂等取消 | 成功/失败终态返回冲突 |
| GET | `/api/v1/workflow-runs/{run_id}/events` | SSE 进度 | `Last-Event-ID`、sequence 续传与去重 |
| POST/GET | `/api/v1/document-tasks` | 创建/查询文档任务 | 预算、引用、人工审核 |
| GET | `/api/v1/assets/{id}` | 资产元数据 | 下载前再次授权 |

服务端从会话生成 `PermissionSnapshot`。OpenAPI 中出现该类型是为了 Worker 内部契约和测试，公开客户端传入的同名字段不得覆盖服务端快照。

错误码至少覆盖：`AUTH_REQUIRED`、`PERMISSION_DENIED`、`VALIDATION_FAILED`、`IDEMPOTENCY_CONFLICT`、`BUDGET_EXCEEDED`、`MODEL_UNAVAILABLE`、`WORKFLOW_INVALID`、`DOCUMENT_PARSE_FAILED`、`ASSET_NOT_FOUND`。供应商原始报错映射后返回，不泄露请求或凭证。

## 5. 一期验收基线

| 指标 | 基线 |
|---|---:|
| 标准知识问题 | ≥ 50 条 |
| 权限泄漏 | 0 |
| 回答引用覆盖率 | ≥ 90% |
| 核心问题业务认可率 | ≥ 80% |
| Workflow/文档任务成功率 | ≥ 90% |
| 正式资产可追溯率 | 100% |
| 模型调用 Trace 覆盖率 | 100% |
| 费用归属率 | 100% |
| 破坏性契约变更未提升版本 | 0 |
| AWS → 企业环境演练 | 数据、对象、权限、回滚全部通过 |

验收用例必须包含：无权用户检索、跨部门资产访问、重复幂等请求、SSE 断线续传、模型超时/降级、人工审核暂停/恢复、供应商切换但历史价格不变、恶意 Workflow 环与非法节点、文件解析失败和对象存储短时 URL 过期。

## 6. 交付门禁

1. Schema、TypeScript 类型、OpenAPI、示例一致且可解析；生成 SDK 无未提交漂移。
2. 单元测试 → 契约测试 → 权限测试 → 集成测试 → 50 条评测集依次通过。
3. 每轮代码保持未提交先交叉 CR；BLOCK 返回原作者，ALLOW 后立即提交该轮。
4. 集成冲突由文件 Owner 修改；同一文件在同一轮只允许一个开发 Agent 负责。
5. 正式发布前完成依赖许可证、SBOM、镜像扫描、密钥扫描、备份恢复和回滚演练。
