# 贡献指南

## 依赖管理

依赖的唯一来源是 `pyproject.toml`：运行时依赖写在 `[project.dependencies]`，可选组包括 `dev`、`rerank`、`docs` 和仅供旧快照重放的 `legacy-eval`。

`uv.lock` 是提交进仓库的锁定版本清单，用于复现完整依赖树。CI 会跑 `uv lock --check`，一旦它与 `pyproject.toml` 不同步就会失败。

```bash
uv sync --extra dev          # 按 uv.lock 精确复现开发环境（推荐）
uv lock --check              # 改过 pyproject 后校验锁文件是否同步

pip install -e ".[dev]"      # 开发（含 pytest、ruff、mypy）
pip install -e ".[rerank]"   # 本地 bge 召回 + Cross-Encoder 精排
pip install -e ".[docs]"     # Unstructured 解析 PDF/Office/HTML/图片
pip install -e ".[dev,legacy-eval]" # 历史图运行时与 P0 快照重放
```

## 代码规范

项目使用 Python 3.11+，采用 Ruff 进行格式化、导入排序和静态检查，并用 mypy 做类型检查。注释说明业务约束、失败处理和设计取舍；接口信息优先通过类型、schema 和必要的文档字符串表达。

```bash
ruff format .
ruff check .
mypy
python -m compileall -q app eval
pytest -q
```

CI 会在 Python 3.11 与 3.12 上依次跑 Ruff、mypy 与 pytest，并要求 `app` / `eval` 的覆盖率不低于 70%。

提交前排除 `.env`、密钥、本地数据库、缓存及 `eval/runs/` 中的临时结果。经过审阅的合成评测证据可随报告保存在 `eval/experiments/`，明确版本、数据与测量边界。业务逻辑应放在分层目录中，通过 schema 传递数据，避免在路由层直接访问基础设施。

冻结数据、应用源码和裁判有字节摘要，`.gitattributes` 对相关路径保留原始字节。整理文件时不要改动冻结内容、覆盖历史结果或仅为了更新行尾重新生成快照；新实验采用新版本和新目录。
