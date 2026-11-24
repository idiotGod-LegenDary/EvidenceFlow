# EvidenceFlow 文档

运行与配置见 [项目 README](../README.md)。当前实现与实验按以下入口阅读：

| 内容 | 文档 |
|---|---|
| 请求阶段、规划协议与工具边界 | [请求运行时](23-runtime.md) |
| 项目定位、独立贡献与来源 | [项目介绍与能力归属](18-project-positioning.md) |
| 工单、确认、事务与恢复 | [工单工作流](20-case-workflow.md) |
| 多轮澄清、政策引用与版本对照 | [对话与评测设计](22-dialogue-and-controlled-evaluation.md) |
| 任务数据、在线配置、用量与判分 | [业务任务评测](21-service-evaluation.md) |
| 知识增量更新与版本一致性 | [知识快照](17-knowledge-snapshots.md) |
| 当前运行时验证 | [0.2 验证报告](../eval/experiments/RUNTIME_V2.md) |
| 历史受控对照 | [P0 验证报告](../eval/experiments/P0_VERIFICATION.md) |
| 全部实验与历史结果 | [实验目录](../eval/experiments/README.md) |

## 代码位置

| 目录 | 用途 |
|---|---|
| `app/business/` | 订单网关、对话解析与状态 |
| `app/services/` | 工单与聊天服务、依赖装配 |
| `app/runtime/` | 请求协调、受限规划、只读调度与证据回答 |
| `app/retrieval/` | 知识构建、检索、排序与快照 |
| `alembic/` | 数据迁移 |
| `frontend/` | 售后工单工作台 |
| `eval/` | 任务集、裁判、版本快照和复现入口 |
| `tests/` | 单元、集成与接口回归 |

## 历史参考

[参考索引](reference-index.md) 收录基础模块与历史实现说明；[上游资料](../README.upstream.md) 和 [来源记录](../UPSTREAM.json) 保留开源归属。历史结果的环境、版本和口径以各自报告为准。
