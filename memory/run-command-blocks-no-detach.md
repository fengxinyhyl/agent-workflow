# run 阻塞无 detach，但调用方超时不会杀掉 Popen 子进程树

`cli.py` 的 `cmd_run`：`run_id = runner.start()` 后紧跟 `final_state = runner.run()`，**同步阻塞跑到终态才返回**，CLI 无 `--detach`/daemon 子命令。每个 agent 由 `base.py:102` 的 `subprocess.Popen` 起独立子进程，主进程 `while process.poll() is None` 每秒轮询。

## 关键事实（2026-07-13 实测）

单条工作流墙钟 30~90 分钟（requirement-understanding 1.5h+），远超调用方"后台任务"的分钟级超时（Claude Code 后台工具 10 分钟）。**但调用方超时到点不等于工作流被杀**：

实测 `cli run` 挂在 Claude Code 后台工具下，工具 10 分钟后返回"超时"，而 `run` 主进程 + agent 子进程作为脱离进程**继续跑满约 50 分钟到正常推进**（execution/output_review 单节点超 10 分钟均完成）。原因：调用方工具超时只停止它对该任务的跟踪，**不级联 kill 已 Popen 的进程树**（至少 Windows + Git Bash 下）。

## 结论

- 调用方（AI 助手 / CI / 脚本）挂后台跑 `run` 是可行的；**收到工具超时返回后别 kill 进程、别重启**，靠 `status`/`log`/`heartbeat.json` 判活取进度。
- 无 detach flag 不是致命坑——阻塞进程被后台化后照样活。若确实想要"启动即返回 run_id"的干净语义，可考虑加 `run --detach`（`start()` 后 spawn 子进程接管 `run()`，父进程打印 run_id 即退），但非必需。

排查同类现象先分清三层超时：①调用方工具超时（只停跟踪、不杀脱离进程）②节点 `timeout_seconds`（base.py deadline）③workflow `guards.max_duration_minutes`。曾误把①当成"进程被杀"下错结论，勿重蹈。
