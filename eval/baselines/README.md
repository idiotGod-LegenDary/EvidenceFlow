# 应用快照

`pre_p0.zip` 是 P0 修改前实际可运行的 EvidenceFlow 应用；`p0_candidate_v1.zip` 是首次封存评测候选。快照仅包含 `app/**/*.py`、技能 JSON 与 MIT 许可证，不包含密钥、数据库、模型权重或本机工具。

`p0_final.zip` 是诊断后补修版本，对应最终本地回归；不使用首次封存候选的在线分数代替它的在线复测。

基线保留已有的持久化工单、确认复核、数据库事务和结果验证。比较对象为本地改动前后的版本，不是原始 Adaptive-rag 上游的性能对照。

基线与数据摘要在 `eval/dataset/p0_protocol.json` 中固定；每次运行另记录实际源码摘要。完整方法见 `docs/22-dialogue-and-controlled-evaluation.md`。
