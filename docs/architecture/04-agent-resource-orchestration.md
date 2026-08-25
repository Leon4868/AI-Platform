# Agent 资源中心与编排契约

状态：二期实施基线  
适用范围：Agent、Workflow、Prompt、Skill、Knowledge、Model、Tool、MCP、评测与发布  
原则：资源先注册和发布，编排只引用有权限的不可变版本；Workflow 为主，Agent 判断为辅。

## 1. 为什么需要资源中心

Agent 编排不能从一张预置画布直接开始。用户必须先创建 Agent，Agent 再绑定模型、Prompt、知识库、Skill 和允许使用的工具；Workflow 负责把 Agent、工具、控制节点和人工审批组合成可恢复、可审计的执行过程。

当前系统已有 Workflow、运行事件、知识库、模型网关和资产底座，但还缺少 Agent 聚合根、Prompt/Skill 版本、Tool Registry、MCP Server Registry、能力审核、资源发布和评测门禁。本文件是这些能力的统一业务契约。

## 2. 领域关系

```text
Secret Reference
├── Model Provider Credential
├── HTTP Tool Credential
├── MCP Server Credential
└── Connector Credential
          │
          ▼
Resource Center
├── Logical Model
├── Knowledge Base
├── Prompt Version
├── Skill Version
├── Tool Version
│   ├── Built-in Tool
│   ├── HTTP/OpenAPI Tool
│   └── MCP Tool
├── MCP Resource / Prompt
└── Connector
          │
          ▼
Agent Version
├── Model Reference
├── Prompt Reference
├── Knowledge References
├── Skill References
├── Allowed Tool References
├── Limits and Approval Policy
└── Owned Workflow Version
          │
          ▼
Workflow Version
├── Agent / Sub-Agent Node
├── Prompt / Model / RAG Node
├── Tool / MCP Tool Node
├── Condition / Parallel Node
├── Human Approval Node
└── Asset Publish / Output Node
          │
          ▼
Evaluation → Approval → Gray Release → Published Version → Run / Trace / Audit
```

## 3. 核心实体

### 3.0 Definition、Draft 与 Version

可发布资源（Agent、Prompt、Skill、Tool、Model Route）统一使用两层状态，不再用一个 `status` 同时表达“有生产版本”和“当前是否有未发布修改”。MCP Server、Knowledge Base 等运行资源使用各自生命周期：

- **Definition/Draft** 是可编辑主体，保存 `lifecycleStatus=active|archived`、`availabilityStatus=enabled|deprecated|disabled|revoked`、单一 `aggregateRevision`、`publishedVersion` 与 `hasUnpublishedChanges`；Agent 配置和其 owned Workflow 的任何修改都递增同一个 aggregate revision；
- **Version Content** 只在发布事务中创建，创建后永不可修改；可变的 `VersionAvailability=active|retired|runtime_blocked` 存在独立覆盖层，只控制能否新绑定/运行，不修改快照内容；
- `deprecated` 阻止新的依赖绑定和发布，既有发布版本及在途 Run 可继续；`disabled/revoked` 在下一次副作用边界立即拒绝；`archived` 仅隐藏入口并保留历史；这些都是 Definition 的可变运行控制状态，不属于 Version 内容；
- 名称、Schema、引用、画布或策略发生变化时递增 `aggregateRevision`，不能覆盖已发布 Version。

并发发布必须锁住资源的逻辑键，并在同一事务中完成版本号分配与 Version 写入。PostgreSQL 实现不能依赖 `SELECT FOR UPDATE` 锁一个尚不存在的行，必须使用逻辑键 advisory lock、`SERIALIZABLE` 加冲突重试，或锁定确定存在的父 Definition 行。

### 3.1 Agent

Agent 是员工看到和使用的 AI 应用，也是版本、权限、负责人和发布状态的聚合根。`POST /agents` 必须在一个事务中创建 Agent、Agent Draft 与其唯一私有 Workflow Draft，失败时整体回滚，不能产生孤儿画布。Agent 根节点不能出现在自己的 Workflow 中；二期不允许多个 Agent 共用可编辑 Workflow，也不允许独立 Workflow 绕过 Agent 直接运行。

| 字段 | 说明 |
|---|---|
| `id` / `tenantId` | 租户内稳定标识 |
| `name` / `description` | 名称与用途 |
| `ownerDepartmentId` / `createdBy` | 责任部门与创建人 |
| `lifecycleStatus` | `active`、`archived` |
| `aggregateRevision` / `hasUnpublishedChanges` | Agent 配置和 owned Workflow 共用的乐观锁修订号与未发布修改标记 |
| `publishedVersion` | 当前生产 Version，可为空 |
| `ownedWorkflowDraftId` | Agent 独占画布标识；画布写入同样校验 aggregate ETag |
| `createdAt` / `updatedAt` | 审计时间 |

发布 Agent 时必须在一个事务中同时生成不可变的 Agent Version、其独占的 Workflow Version 和 Dependency Manifest。生产回滚只移动 `publishedVersion` 指针，不能修改旧版本。Agent Version 至少包含：

- 系统指令或 Prompt Version 引用；
- Logical Model 与 Model Route Version；
- Knowledge Base 引用与检索策略；
- Skill Version 引用；
- Allowed Tool Version 引用；
- `maxSteps`、`maxToolCalls`、`maxRunSeconds`、`maxCost`；
- `allowExternalNetwork`、`writeActionApproval`；
- 同一发布事务生成的 Workflow Version 引用；
- 发布人、审批人、评测结果与变更说明。

Dependency Manifest 保存全部依赖的精确版本、Schema/graph hash 与权限需求。发布校验必须拒绝 Agent 自引用、跨 Agent 引用环，并配置最大嵌套深度；子 Agent 节点只能引用 `agentId + published version`。

### 3.2 Prompt

Prompt 是独立的版本化企业资产，不允许 Agent 将长提示词只保存在节点自由文本中。

- 草稿可编辑；发布版本不可变；
- 声明输入变量、默认值、必填项和输出 Schema；
- 保存示例、测试用例、变更说明与安全标签；
- 发布 Agent 时锁定 `promptId + promptVersion`。
- Prompt 节点只负责确定性模板渲染，不执行网络调用，也不能被映射为 Tool。

### 3.3 Skill

Skill 是可复用能力包，不是任意脚本：

```text
Skill Version
├── Instruction / Prompt Reference
├── Input / Output JSON Schema
├── Knowledge References
├── Allowed Tool References
├── Examples
├── Evaluation Cases
└── Safety Limits
```

二期 Skill 仅作为 Agent 的配置能力包被引用，不是 Workflow 节点，也不在运行时动态展开。未来若引入 Skill 节点，必须另行定义确定性的展开版本、输入输出映射、权限合并和 Trace 规则。

### 3.4 Tool Registry

Tool Registry 统一管理三类工具：

| 类型 | 来源 | 二期边界 |
|---|---|---|
| `builtin` | 平台受控实现 | 允许 |
| `http` | HTTP/OpenAPI | 允许，必须配置域名白名单与 Secret Reference |
| `mcp` | MCP Server 能力发现 | 允许，必须审核并固化 Schema |

Tool Version 至少包含：

- `toolId`、`version`、`sourceType`、`sourceRef`；
- 输入与输出 JSON Schema；
- Schema Hash；
- `effect=read|write|destructive` 与独立的 `risk=low|medium|high|critical`；
- 超时、重试、并发、幂等策略；
- 网络出口白名单；
- `no_approval_required`、`approval_on_write`、`approval_always` 审批策略；
- Definition 状态与不可变 Version 状态遵循 3.0，不把 `verified` 或 `disabled` 写进发布快照状态。

HTTP Tool 注册时必须验证 `baseUrl` 属于出口白名单。真正调用前和每次重定向后都要重新解析 DNS、默认拒绝私网/环回/链路本地地址并限制跳转次数，防止 SSRF、DNS rebinding 和利用重定向逃逸白名单。企业内网 MCP 只能由管理员绑定到受控网络区域与固定 CIDR，不能由普通用户通过 endpoint 绕过默认拒绝策略。

Workflow 中引用的 Tool 必须属于根 Agent 的 `allowedTools`；Skill 声明的 Tool 同样必须是该 allowlist 的子集。非幂等写操作禁止自动重试，只有声明并验证幂等键语义的写操作才能按策略重试。MCP Tool 只是 `sourceType=mcp` 的 Tool Version，不建立第二套可运行工具实体。

节点审批策略只能等于或严于 Tool Version 和平台策略的最低要求，不能降级。HTTP/MCP 输出一律视为不可信输入，必须限制字节数和结构深度、验证输出 Schema、转义展示，并在进入模型上下文时使用独立的不可信数据通道和 Prompt Injection 防护。

任意 Shell、Python、JavaScript 或宿主文件系统执行不属于二期 Tool 类型。

### 3.5 MCP Server Registry

MCP Server 是连接配置与能力来源，不等于具体 Tool。

| 字段 | 说明 |
|---|---|
| `transport` | 二期默认 `streamable_http`；`stdio` 只允许隔离 Worker |
| `endpoint` | 服务端地址，禁止用户在画布中临时填写 |
| `credentialBindingId` | 平台创建的 opaque 凭证绑定 ID；不接受任意 URI，也不返回密钥值 |
| `egressPolicy` | 域名/IP 白名单与网络策略 |
| `verificationStatus` | `draft`、`verified` |
| `enablementStatus` | `enabled`、`disabled` |
| `healthStatus` | `unknown`、`healthy`、`unhealthy` |
| `capabilityRevision` | 最近一次能力快照版本 |
| `lastHealthCheckAt` | 最近健康检查时间 |

`McpServerDefinition` 保存身份、租户与 ACL；可编辑连接配置每次变更生成不可变 `McpServerConfigRevision`，运行绑定精确 revision；健康状态与启停状态是独立可变覆盖层，不写回配置快照。Knowledge Base 的内容 revision、索引健康和访问策略也分别演进，不套用可发布资源状态机。

MCP 同步流程：

```text
注册 Server
→ 测试连接
→ 拉取 Tools / Resources / Prompts
→ 保存 Capability Snapshot
→ 管理员审核风险与 Schema
→ 发布 Tool Version
→ Agent / Workflow 才可引用
```

MCP 能力快照不可变，Schema Hash 必须覆盖 capability 的名称、描述、annotations、输入和输出 Schema。能力发生漂移时，旧 Tool Version 继续保留但禁止静默切换；运行前 Hash 不匹配必须阻断调用，新 Schema 必须重新审核和发布。

MCP Tool Version 必须锁定 `mcpServerConfigRevision + capabilityRevision + capabilityName + capabilityHash`。同步发现漂移后把旧版本的独立 `VersionAvailability` 覆盖为 `runtime_blocked`，不修改 Version Content；Server 禁用后其能力在下一次调用立即拒绝。外部 MCP Resource/Prompt 不会自动成为企业 Knowledge/Prompt，二期仅允许发现和隔离保存，导入、内容扫描、人工审核与平台版本化完成后才能使用；Connector 运行语义不在二期范围。

`stdio` 只允许运行平台管理的固定镜像与入口：非 root、无宿主文件系统、临时文件系统、默认断网、CPU/内存/进程/时长限制、短时凭证挂载，并按租户和 Run 隔离。

### 3.6 Model 与 Knowledge

- 业务只引用 Logical Model，不直接绑定供应商 Key；发布快照记录 Route Version。
- Knowledge 节点必须先做租户、部门、项目、密级 ACL，再进行向量召回、RRF 和 Rerank。
- Knowledge 依赖必须显式选择 `live` 或 `snapshot`：`live` 发布时绑定知识库身份，Run 记录实际 `indexRevision`、资产/文档版本与 Citation；`snapshot` 发布时固定 `knowledgeSnapshotId + policyVersion + retrievalConfigVersion`，用于强复现和受监管场景。两种模式都不能静默跨越权限策略版本。
- 任何外部 Reranker 只能收到已通过 ACL 的候选内容。

### 3.7 权限模型

资源权限动作统一为 `view`、`use`、`edit`、`publish`、`admin`、`approve`，由服务端根据租户、所有者、责任部门、`projectId`、`dataScope`、`securityLevel`、maintainers 和显式共享 Grant 计算；前端传来的部门 ID 或权限声明不能直接采信。责任部门只能从当前用户有管理权的部门集合中选择，发布审批必须满足 approver 权限和职责分离策略。

嵌套 Agent 的有效权限为“当前用户权限 ∩ 父 Agent 执行策略 ∩ 子 Agent 权限需求 ∩ Tool/Knowledge 当前 ACL ∩ 平台安全策略”，子 Agent 不能扩大数据范围、密级、网络出口或工具权限。每次模型调用、知识检索、Tool/MCP 调用和资产写入前重新检查授权；`disabled/revoked` 也在这些副作用边界逐次检查并立即终止后续副作用。

## 4. 编排页面交互

```text
┌────────────────┬────────────────────────────┬──────────────────┐
│ 节点与资源抽屉   │ Agent：企业文档助手 · 草稿 v3 │ 节点属性抽屉      │
│                │                            │                  │
│ [节点] [资源]    │ 输入 → 检索 → 文档专家 Agent │ 资源与版本         │
│                │              ↓             │ 输入/输出映射       │
│ Agent          │ MCP Tool → 人工审核 → 归档  │ 超时/重试/审批      │
│ Prompt         │                            │ 风险与权限          │
│ Knowledge      │                            │                  │
│ Tool / MCP     │                            │                  │
└────────────────┴────────────────────────────┴──────────────────┘
```

左侧抽屉包含：

1. **节点 Tab**：创建通用 Agent、Prompt、RAG、Tool、条件、并行、审批、输出等节点；
2. **资源 Tab**：列出当前身份有权使用的已发布 Agent、Skill、Prompt、Knowledge、Tool 与 MCP Tool；Skill 只能绑定到 Agent 配置，不能拖成画布节点。

用户可以拖入节点后在右侧选择资源，也可以直接拖入已发布资源，自动生成预绑定节点。

右侧属性面板统一配置：

- 资源 ID 与精确版本；
- 输入映射、输出映射和 JSON Schema；
- 超时、重试、幂等、失败分支；
- 风险级别与审批策略；
- 权限范围和数据范围；
- 运行成本上限。

未选择 Agent 时，画布和试运行按钮必须禁用；创建 Agent 草稿成功后自动进入该 Agent 的编排画布，不再要求用户手填 Workflow UUID。

## 5. 单 Agent 与多 Agent

单 Agent 的 Workflow 可以直接使用模型、Prompt、RAG 与 Tool 节点。多 Agent Workflow 使用已发布的 Agent Version 作为子 Agent 节点：

```text
员工输入
→ 意图分类 Agent v2
  ├── 周报 → 周报专家 Agent v4
  ├── 制度 → 制度专家 Agent v3
  └── 其他 → 人工处理
→ 内容审核
→ 企业资产归档
```

父 Workflow 必须保存子 Agent 的精确版本、输入输出 Schema 和权限需求。生产运行禁止使用 `latest`。

## 6. 发布与运行约束

发布门禁必须验证：

- 所有引用资源均存在、已发布且当前身份可使用；
- Prompt 变量、节点输入输出 Schema 和边连线兼容；
- Tool/MCP Schema Hash 未漂移；
- 写入和高风险工具存在人工审批；
- Agent、Workflow、Tool 和 Model 的成本/时长上限完整；
- 至少一个评测集通过约定阈值；
- 没有明文密钥、任意代码执行或未授权网络出口。

发布状态机为 `draft → validated → release_candidate → evaluated → approved → published`。`POST /publish` 只接受已通过的 `gateReportId + evaluationResultId + approvalId`，并验证审批人与提交人职责分离；灰度发布通过独立 Release Binding 保存目标 Agent Version、目标部门/用户群、流量比例、生效窗口、健康阈值和自动回滚版本，不修改 Agent Version Content。若租户策略明确关闭评测或审批，对应 Gate Report 必须记录被哪条策略豁免，不能由客户端省略。

一次 Run 的快照至少保存：

- Agent、Workflow、Prompt、Skill、Tool 的精确版本，Workflow graph/dependency manifest hash，以及 Knowledge live 实际 revision 或 snapshot ID；
- MCP Capability Revision 与 Schema Hash；
- Model Route Version、实际 Provider/Model 与价格快照；
- Permission Snapshot；
- Run/Trace ID、节点状态、审批、Token、成本和资产血缘。

每次模型调用、知识检索、Tool/MCP 调用和资产写入前都要在服务端重新授权并检查 `disabled/revoked`，不能只相信发布时或启动时的权限。历史 Run 仅可回放已记录事件，不得借回放再次产生外部副作用。

## 7. API 分组

首批 API 使用 `/api/v1`：

| 分组 | 主要接口 |
|---|---|
| Agents | `POST/GET /agents`、草稿/画布/校验/候选/评测/审批/发布、生产指针与 Run |
| Agent Versions | `GET /agents/{id}/versions`、`GET /agents/{id}/versions/{version}` |
| Prompts | `POST/GET /prompts`、版本、测试、发布 |
| Skills | `POST/GET /skills`、版本、测试、发布 |
| Tools | `POST/GET /tools`、验证、版本、发布、禁用 |
| MCP Servers | `POST/GET /mcp-servers`、`test`、`sync`、`capabilities`、`enable/disable` |
| Workflows | Agent-owned 草稿保存与版本读取；不提供独立发布或新 Run 入口 |
| Evaluations | 数据集、执行、结果与发布门禁 |

所有接口必须鉴权和租户隔离，所有写操作必须写审计日志；命令型 POST 使用幂等键，草稿 PUT/PATCH 使用乐观锁。列表接口必须分页且只返回当前身份可见的数据。

### 7.1 首批可测试契约

| 接口 | 请求/成功响应 | 关键错误 |
|---|---|---|
| `POST /agents` | `name,description,ownerDepartmentId` → `201 AgentSummary`，包含 `ownedWorkflowDraftId,aggregateRevision` 和 aggregate ETag | `422 validation`、`403 department_forbidden`、`409 idempotency_conflict` |
| `GET /agents?offset&limit` | `200 {items,total,offset,limit}`，仅返回当前身份具备 `use` 权限的资源 | `400 invalid_page` |
| `GET/PATCH /agents/{id}/draft` | GET 返回 aggregate `ETag`；PATCH 携带 `If-Match` → `200 AgentDraft` 与新 aggregate ETag | `403 edit_forbidden`、`404 not_found`、`412 revision_conflict` |
| `GET/PUT /agents/{id}/workflow-draft` | GET 返回 graph 与同一个 aggregate ETag；PUT 携带 `If-Match` 保存画布并递增 aggregate revision | `403 edit_forbidden`、`412 revision_conflict`、`422 graph_invalid` |
| `POST /agents/{id}/validate` | `200 ValidationReport` | `422 dependency_or_graph_invalid` |
| `POST /agents/{id}/test-runs` | 携带 aggregate `If-Match` → `202 RunSummary`，不移动生产指针 | `412 draft_changed`、`422 validation_failed` |
| `POST /agents/{id}/release-candidates` | 携带 aggregate `If-Match` → `201 ReleaseCandidate`，固化 gate report | `412 revision_conflict`、`422 gate_failed` |
| `POST /agents/{id}/evaluations` / `POST /agents/{id}/approvals` | 对候选执行评测和审批，返回不可变结果 | `403 approve_forbidden`、`409 candidate_state_conflict` |
| `POST /agents/{id}/publish` | `candidateId,gateReportId,evaluationResultId,approvalId,changeNote` → `201 AgentVersion` | `403 publish_forbidden`、`409 candidate_state_conflict`、`422 publish_gate_failed` |
| `POST /agents/{id}/production-pointer` | `version,expectedCurrentVersion,expectedRevision,reason` → `200 AgentSummary`；移动前重验目标依赖可用性 | `403 publish_forbidden`、`409 pointer_conflict`、`422 version_or_dependency_unavailable` |
| `POST/PATCH /agents/{id}/release-bindings` | 创建/调整灰度人群、比例、窗口、阈值与回滚版本；PATCH 携带 ETag | `403 publish_forbidden`、`412 revision_conflict`、`422 invalid_rollout` |
| `POST /agents/{id}/runs` | 先按当前身份适用的 Release Binding 解析灰度版本，否则使用 `publishedVersion`，创建 `202 RunSummary` | `409 agent_disabled`、`422 no_published_version` |
| `POST /tools` | 原子创建 Tool Definition 与包含 effect/risk/审批/出口策略的初始 Draft → `201 ToolSummary` | `403 admin_forbidden`、`422 unsafe_endpoint` |
| `POST /tools/{id}/publish` | 原子创建不可变 Tool Version → `201 ToolVersion` | `409 version_conflict`、`422 verification_failed` |
| `POST /mcp-servers` | endpoint、transport、`credentialBindingId` → `201 McpServerSummary` | `422 unsafe_endpoint`、`422 invalid_credential_binding` |
| `POST /mcp-servers/{id}/sync` | `202 Operation`，完成后产生不可变 Capability Snapshot | `409 sync_in_progress`、`422 capability_invalid` |
| `POST /mcp-servers/{id}/capabilities/{capabilityId}/publish` | Snapshot → `201 ToolVersion` | `409 schema_drift`、`422 review_required` |
| `GET /orchestration/resources` | 按 kind/keyword/cursor 返回当前身份可 `use` 的精确已发布版本 | `400 invalid_filter` |
| `POST /workflow-runs/{id}/approvals` / `POST /workflow-runs/{id}/cancel` | 审批或取消命令 → `200 RunSummary` | `403 approve_forbidden`、`409 state_conflict` |
| `GET /workflow-runs/{id}/events` / `GET /traces/{id}` | ACL 过滤后的只读事件与 Trace | `403 view_forbidden`、`404 not_found` |

命令型 POST 接收 `Idempotency-Key`；同租户、同身份、同路由、同规范化请求体重放返回第一次响应，请求体不同返回 `409 idempotency_conflict`。草稿更新使用 `ETag/If-Match` 乐观锁而不是幂等键。错误统一使用 `application/problem+json`，至少包含 `type,title,status,detail,code,requestId`；只有 Run 已创建时才附 `traceId`。迁移期保留现有 `request_id` 字段一个版本，并同步返回 `requestId`。

旧 `/workflow-runs` 的创建入口在 Agent-owned Workflow 上进入弃用期：本期继续支持既有客户端但响应 `Deprecation/Sunset/Link` 头，新客户端必须使用 `/agents/{id}/runs`；后续版本关闭绕过 Agent 的生产 Run，事件、审批、取消与 Trace 查询仍保留 `/workflow-runs/{id}` 资源路径。

## 8. 存储和删除策略

- Agent、Prompt、Skill、Tool、MCP Server 与版本表均包含 `tenant_id`、审计字段和租户复合约束；
- Workflow 存储必须拆成 `workflow_drafts`、`workflow_versions` 与 `workflow_version_dependencies`；Run 保存不可变 Workflow Version FK、graph hash 和 dependency manifest/hash，迁移完成前不得宣称历史 Run 可复现；
- 外键默认 `RESTRICT`，已被发布版本引用的资源不能物理删除；
- 仅未发布、无引用且无 Run 的草稿允许物理删除；发布版本和 Capability Snapshot 永久保留或按合规策略写 tombstone；
- `deprecated` 阻止新绑定/发布；`disabled/revoked` 在每个副作用边界立即生效；`archived` 只隐藏入口；历史 Run 仍可通过事件快照还原但不能重新产生副作用；
- 业务表可以保存平台生成的 opaque `credentialBindingId`，密钥值只存在 Secret Provider，永不进入业务表、API、日志或版本快照；
- MCP Capability Snapshot 和发布版本不可变。

## 9. 实施顺序

1. Tool/MCP 领域模型、权限与内存 Repository；
2. Agent 聚合根与版本化资源引用；
3. REST API、Problem Details、幂等与审计；
4. PostgreSQL 表和 Alembic migration；
5. 资源中心与 Agent 中心；
6. 画布节点/资源双 Tab、属性配置和版本锁定；
7. MCP Gateway、隔离 Worker 与每次调用重新授权；
8. 评测、审批、灰度、发布与回滚。

## 10. 本轮验收基线

- 项目内存在本文件并作为后续实现契约；
- Tool/MCP 和 Agent 领域模型不保存凭证明文；
- 可创建租户隔离的 Agent 草稿；
- Agent 可引用已发布资源版本，非法或草稿引用被拒绝；
- MCP Tool 与 Server 分离，能力 Schema 可快照和校验；
- 前端能从 Agent 中心创建草稿并进入绑定当前 Agent 的画布；
- 未选择 Agent 时不能编排或试运行；
- 新增能力具有单元测试、契约测试和独立 CR，保持工作区未提交。
