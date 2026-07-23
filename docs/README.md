# 文档索引

## 当前文档

- [`architecture.md`](architecture.md)：组件职责、请求切换状态机、CPU backup 控制面和跨仓库边界。
- [`operations.md`](operations.md)：配置、启动、smoke、workload、观测与清理。
- [`cpu_backup_coordinator.md`](cpu_backup_coordinator.md)：aggregate usage、byte-release 协议、内存压力策略和 failure semantics。

## 历史归档

`archive/` 保存阶段性计划和已经结束的实验。归档文件中的命令、路径、
字段和版本只用于审计当时结果，不是当前 API：

- [`archive/implementation_plan.md`](archive/implementation_plan.md)
- [`archive/exp_001_results.md`](archive/exp_001_results.md)
- [`archive/plans/request-driven-multi-model-serving.md`](archive/plans/request-driven-multi-model-serving.md)

对应的历史实验配置位于 `configs/archive/`。

当前行为以仓库 `README.md`、本目录的当前文档和 CLI `--help` 为准。
