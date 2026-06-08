"""strategy_research — 第一个具体的 long-task workflow 实现。

将策略研究实验生命周期（plan → review → execute → audit → summary）
映射为一组 WorkItem，按 depends_on 顺序 FIFO 执行。
"""
