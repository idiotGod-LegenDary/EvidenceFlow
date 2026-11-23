# 本地验证记录

2026-11-21，Windows，工作区隔离 Python 3.12.10。测试依赖来自本次 pyproject 安装；未按上游 uv.lock 锁定环境，未修改系统 Python。

| 检查 | 结果 |
|---|---|
| `python -m pytest -q --cov=app --cov=eval --cov-report=term:skip-covered --cov-fail-under=70` | 178 passed；总覆盖率 71.89%，达到 70% 门槛 |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 165 个 Python 文件格式检查通过 |
| `python -m mypy` | 125 个源文件无类型错误 |
| PowerShell Parser 检查 `scripts/demo-local.ps1` | 语法通过；未在此验证中启动常驻服务器 |
| `python -m eval.knowledge_lifecycle --repeats 3` | 5 个场景，15 个配对，30 次构建；证据集合及版本一致 |
| 对照上游 ZIP | LICENSE、README.upstream.md 和原知识库文件逐字节不变 |

测试输出保留在 [pytest_20261121.txt](pytest_20261121.txt)。新增 15 项测试，已有 163 项测试同样通过。测试里使用 FastAPI TestClient 验证了实际应用装配、管理权限、热更新接口与对话引用；Redis/Qdrant 验证使用替身，没有运行真实服务或前端浏览器验收。

pytest 有一条来自上游 LangGraph Checkpoint 依赖的待弃用提示，不影响结果。并发与失败测试没有调用真实 LLM；本次没有提供回答质量、真实 Embedding 延迟或生产吞吐的结论。
