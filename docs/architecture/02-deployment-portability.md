# AWS 到企业服务器的可迁移部署

## 1. 原则

AWS 是临时运行环境，不是产品接口。业务代码只能依赖 Port，AWS SDK 只能存在于 Infrastructure Adapter。数据库迁移、对象清单、配置 Schema 和模型路由均纳入版本控制；密钥只保存在环境对应的密钥系统中。

## 2. 适配矩阵

| 能力 | 私人 AWS 一期 | 企业服务器 | 业务侧稳定接口 |
|---|---|---|---|
| 应用运行 | EC2 + Docker Compose（早期） | Docker Compose 或 Kubernetes | OCI image / HTTP |
| PostgreSQL | RDS 或容器化 PostgreSQL | 企业 PostgreSQL HA | SQL + migration |
| 向量 | pgvector | pgvector | KnowledgeRepository |
| 对象存储 | 私有 S3 bucket | MinIO/企业 S3 | ObjectStoragePort |
| 缓存 | ElastiCache/容器 Redis | 企业 Redis | CachePort |
| 队列 | RabbitMQ/Redis（保持中立） | 企业 MQ/RabbitMQ | TaskQueuePort |
| 密钥 | AWS Secrets Manager/KMS | Vault/企业 KMS | SecretProvider |
| 监控 | OpenTelemetry → CloudWatch/Grafana | OpenTelemetry → 企业平台 | OTLP |
| 身份 | 临时 Identity Adapter | 企业微信 Adapter | IdentityProviderPort |
| 模型 | 出站调用三家 API | 同一网关或自托管适配器 | ModelProviderPort |

一期避免 Bedrock Agent、Lambda 工作流、Step Functions、OpenSearch 专有向量接口等深绑定。若为成本或运维临时使用，必须同时提供中立适配器和导出路径。

## 3. 网络与密钥

- 模型请求只由 Model Gateway 发出；其他容器默认无供应商 API 出站权限。
- 每个供应商使用独立密钥引用和额度；Key 不进入数据库、Prompt、Trace、示例或前端。
- S3/MinIO bucket 私有；客户端下载使用短时签名 URL，授权发生在签发之前。
- PostgreSQL、Redis、MQ 只开放私网；管理入口通过 VPN/堡垒机。
- 临时 AWS 中不得放入未经企业批准的真实核心机密数据。

## 4. 迁移步骤与回滚门禁

1. 盘点：固定应用镜像、Schema 版本、配置 Schema、模型映射和对象清单。
2. 预建：在企业环境部署 PostgreSQL/pgvector、Redis、MQ、MinIO/Vault 和观测系统。
3. 演练：恢复脱敏数据库、同步对象、重建向量索引，对比数量与 Hash。
4. 双环境验证：执行契约测试、50 条知识问答集、权限矩阵和文档生成用例。
5. 冻结写入：AWS 进入短期只读，执行最终增量数据库与对象同步。
6. 切换：更新密钥引用、域名和模型网关出站策略；进行冒烟与业务验收。
7. 回滚窗口：保留 AWS 只读环境，RTO 2 小时内可将流量切回；确认后再按保留策略退役。

迁移成功标准：数据库实体数一致、对象 Hash 一致、正式资产血缘完整、权限测试零泄漏、模型请求可追溯、备份恢复演练通过。向量索引允许重建，不作为唯一事实来源。
