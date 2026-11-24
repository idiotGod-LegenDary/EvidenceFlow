# 第三方组件与版权

EvidenceFlow 0.2 的请求协调、规划协议、只读调度、回答证据校验与混合检索协调在本仓库重新实现。当前源码仍包含第三方基础组件，保留其 MIT 许可证与版权声明；独立运行时不代表整个仓库的所有文件均为原创。

## Adaptive-rag 组件

- 来源：[JEen-wang/Adaptive-rag](https://github.com/JEen-wang/Adaptive-rag)
- 导入版本：`2a12a62382a8c3ec033fa9a5b19b9b23b8780ab8`
- 许可：MIT，`Copyright (c) 2026 JEen-wang`
- 完整许可：[LICENSE](LICENSE)
- 导入包摘要和版本记录：[UPSTREAM.json](UPSTREAM.json)

以下目录包含该来源的原文件或修改版本，不能整体声明为本项目原创：

| 保留范围 | 当前用途 |
|---|---|
| `app/api/`、`app/config/`、`app/core/`、`app/infra/` | HTTP 入口、配置、公共类型与基础设施 |
| `app/providers/`、`app/repositories/` | 模型、嵌入适配与基础会话、订单数据访问；部分模型已扩展 |
| `app/services/container.py`、`seed.py` | 依赖装配与示例数据 |
| `app/tools/` 中的工具处理器、依赖装配与权限辅助 | 本地业务工具及适配；`base.py` 和 `registry.py` 已替换 |
| `app/guardrails/` | 输入注入检测与基础边界检查 |
| `app/retrieval/` 的 BM25、向量、缓存、解析、切块、分词与精排等适配组件 | 底层检索与文档处理；`hybrid.py` 和 `fusion.py` 已替换 |
| `knowledge/` 的原始知识文档、部分前端基础组件与构建配置 | 示例语料与界面基础 |

新增的业务网关、对话状态、知识生命周期、工单状态机和工作台可见对应模块的提交记录。`app/runtime/`、`app/services/chat_service.py`、`app/tools/base.py`、`app/tools/registry.py`、`app/retrieval/hybrid.py`、`app/retrieval/fusion.py` 在 0.2 中采用新的实现。

## 退役代码与历史材料

原有 `app/agents/`、`app/skills/`、`app/memory/`、`app/prompts/`、自适应路由、查询改写和 Self-RAG 已移出当前源码。替换前代码与测试保存在 `eval/baselines/pre_independent_runtime.zip`，已有 P0 快照保持原样。历史快照仍含第三方代码，适用相同许可。

这些快照不在应用导入路径上。LangGraph / LangChain 只在 `legacy-eval` 可选依赖中，用于复现旧实验。新运行时的结构和验证范围见 [运行时设计](docs/23-runtime.md) 与 [验证记录](eval/experiments/RUNTIME_V2.md)。

其他 Python、JavaScript 依赖遵循各自许可证，具体版本见 `uv.lock` 和 `frontend/package-lock.json`。
