# `@ai-platform/contracts`

跨前端、API、Worker 与模型网关的语言中立契约包。

## 约定

- `schemas/*.schema.json` 是运行时校验与跨语言代码生成的权威来源。
- `src/types.ts` 是前端和 Node 工具使用的镜像类型；修改 Schema 时必须同步修改并提升契约版本。
- `openapi.yaml` 定义 HTTP/SSE 边界，不暴露供应商密钥或企业身份 Token。
- `examples/` 只放脱敏示例，可用于契约测试和联调夹具。
- 业务请求使用 `logicalModelCode`，供应商与具体模型只记录在 Trace 的解析结果中。

## 兼容性

- 新增可选字段：向后兼容，不提升主版本。
- 删除字段、改变语义、收窄枚举：破坏性变更，提升契约主版本并提供迁移期。
- 任一事件必须带单调递增 `sequence`，消费者按 `(runId, sequence)` 去重。
- SSE 帧固定使用 `id: sequence`、`event: type`、`data: WorkflowRunEvent JSON`；断线重连通过 `Last-Event-ID` 从下一序号继续。
- 运行入口固定为 `POST /api/v1/workflows/{workflow_id}/runs`；取消操作必须幂等，且不能把已成功或已失败的终态改写为取消。
- 知识库由路径参数指定，检索权限快照必须由服务端会话生成；公开请求不接受客户端提交的 `permissionSnapshot`。
- 工具调用和资产下载前必须实时重新授权；运行记录中的 `permissionSnapshot` 只用于历史审计。
