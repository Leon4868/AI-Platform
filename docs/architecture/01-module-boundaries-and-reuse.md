# 模块边界与复用规则

## 1. 建议目录

```text
apps/
  web/                      # 页面、设计系统、Workflow 画布
  api/                      # FastAPI 控制面、当前进程内 Runtime
packages/
  contracts/                # 当前共享契约（唯一跨端协议入口）
docs/architecture/          # 架构决策与验收门禁
scripts/                    # 契约对齐与全量验收入口

# 二期按真实复用和部署需要再增加：
services/worker/            # LangGraph、RAG、异步渲染
services/model-gateway/     # 独立模型网关
packages/ui/                # 跨应用 UI 包
infra/kubernetes/           # 企业服务器部署
```

## 2. 领域所有权

| 模块 | 负责 | 不负责 | 只通过什么协作 |
|---|---|---|---|
| Web | 页面状态、液态玻璃组件、Workflow 画布、SSE 展示 | 权限判定、模型路由 | OpenAPI、Run Event |
| Identity/Policy | 临时账号/企业微信适配、RBAC、数据范围、策略版本 | 页面隐藏逻辑替代服务端鉴权 | `PermissionSnapshot` |
| Knowledge | 上传、解析、切分、索引、权限前置过滤、引用 | 文档审批与发布 | Knowledge/Citation contract |
| Workflow | Definition 版本、节点校验、运行/恢复/人工暂停 | 任意代码执行、业务资产所有权 | Workflow/Run/Event contract |
| Document | 模板、生成任务、格式渲染、草稿 | 知识召回实现、供应商 API | Document/Citation/Asset contract |
| Asset | 元数据、版本、血缘、权限、审核、发布 | 生成逻辑 | Asset contract |
| Model Gateway | 逻辑模型解析、重试/限流、用量与价格快照 | 用户/部门主数据 | ModelUsage、Trace |
| Audit/Cost | 不可变操作账本、费用归集 | Debug Trace 详情 | requestId、traceId、actorId |

依赖方向固定为：`Web → API → Application Use Cases → Domain Ports → Infrastructure Adapters`。领域模块之间不能直接访问彼此的数据表，只能调用用例接口或消费版本化事件。

## 3. “复用两次以上才抽离”

满足以下全部条件才抽离：

1. 已出现至少两个真实调用方，而不是预测未来可能复用；
2. 两处语义、权限和失败策略相同；
3. 抽离后公共 API 小于重复实现，且不引入双向依赖；
4. 有独立单元测试或 Story/契约夹具。

### 前端可抽离项

| 出现两次的能力 | 抽离目标 | 边界 |
|---|---|---|
| 暗色玻璃容器 | `GlassPanel` / `GlassDialog` | 只含视觉与可访问性，不含业务请求 |
| 列表加载/空/错态 | `AsyncState` | 不替业务模块决定重试策略 |
| 任务状态展示 | `StatusBadge` + 状态映射 | 状态枚举来自 contracts |
| 权限控制 | `PermissionGate` | 仅改善 UI；服务端仍强制授权 |
| 异步任务轮询/SSE | `useRunEvents` | 统一断线续传、sequence 去重、取消 |
| 知识库/资产选择 | `KnowledgePicker` / `AssetPicker` | 选择器不缓存越权对象 |
| API 写操作 | `useIdempotentMutation` | 生成幂等键、错误映射，不吞异常 |
| Workflow 节点外壳 | `WorkflowNodeShell` | 节点配置表单仍归各节点模块 |

Stitch 输出只用于校准 token、间距、阴影、模糊和动效；前端统一使用 Tailwind CSS v4 的 CSS-first 配置，颜色、间距、阴影和动效落在 `styles/globals.css` 的 `@theme` 中。跨页面复用的视觉能力封装为公共 UI 组件、`@utility` 或 `styles/variants.ts`，业务组件不得复制长 class 串。禁止把每个页面截图式硬编码为独立组件。`backdrop-filter` 必须有无模糊降级，文字对比度至少 WCAG AA，`prefers-reduced-motion` 下关闭非必要动效。

### 后端可抽离项

| 出现两次的能力 | 抽离目标 | 边界 |
|---|---|---|
| 外部文件存取 | `ObjectStoragePort` | `put/getSignedUrl/delete/version`，不暴露 S3 类名 |
| 模型调用 | `ModelProviderPort` | 统一结构化输出、用量、超时、取消 |
| 身份同步 | `IdentityProviderPort` | 临时账号与企业微信共享接口 |
| 任务提交与消费 | `TaskQueuePort` | 业务状态仍落 PostgreSQL |
| 审计记录 | `AuditSink` + middleware | Debug Trace 与正式审计分开 |
| 幂等写 | `IdempotencyService` | 以 actor + route + key 定位 |
| 分页/错误/请求元数据 | shared application primitives | 错误码稳定，不泄露供应商原始错误 |
| 供应商策略 | `ModelGateway` | 业务模块只传 logicalModelCode |

不要提前抽象通用 Repository、万能 Workflow Node、万能 JSON `config` 服务或跨领域 BaseService。它们通常掩盖不同权限和事务边界。

## 4. 文件所有权与并行开发

- Contracts Owner 独占 `packages/contracts/**`；任何字段变更先发契约变更说明，再由 Owner 修改。
- Web Owner 独占 `apps/web/**`；Runtime/Control Plane Owner 按领域目录负责 `apps/api/app/**`。二期拆出 Worker 后再迁移到独立服务目录。
- 根依赖文件、锁文件与部署文件只有 Integration Owner 修改；其他人通过消息提出依赖申请。
- 同一文件在一个开发周期只能有一个负责人。集成冲突退回原负责人解决，集成人不代改领域文件。
- 每个切片保持未提交状态先交叉 CR；BLOCK 返回原开发者修改，ALLOW 后立即提交该切片。集成分支再跑契约与端到端测试。
