# 本地无凭证验证

统一入口：

```bash
./scripts/verify.sh all
```

也可按范围运行：

```bash
./scripts/verify.sh contracts   # JSON Schema 示例 + FastAPI/OpenAPI 对齐
./scripts/verify.sh web         # 前端类型、单测、生产构建
./scripts/verify.sh api         # FastAPI 单元/API 测试
./scripts/verify.sh integration # 跨目录契约与运行态测试
```

脚本只使用开发身份、内存存储和无外部模型执行器，不读取模型 API
凭证，不依赖 Docker。`uv` 会根据 `apps/api/pyproject.toml` 创建隔离环境。

OpenAPI 漂移也可单独检查：

```bash
uv run --project apps/api --extra dev python scripts/check_contract_alignment.py
```
