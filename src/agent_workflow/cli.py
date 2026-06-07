"""agent-workflow CLI 入口。

P0 CLI 命令：
  validate-config       校验配置文件
  validate-state-machine  校验状态机完备性
  smoke                 单个 Agent/Role 冒烟测试
  run                   启动 workflow
  status                查看运行状态
  explain               解释当前等待项和可能的后续状态
  log                   查看运行日志
  tail                  查看节点日志
  retry                 重试（默认 dry-run）
  cancel                取消运行
"""

import argparse
import sys


def safe_print(*args, **kwargs):
    """安全打印，fallback 到 ASCII 以兼容 Windows 默认终端编码。"""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        # 将无法编码的字符替换为 ?
        safe_args = []
        for a in args:
            if isinstance(a, str):
                safe_args.append(a.encode(sys.stdout.encoding or 'ascii', errors='replace').decode(sys.stdout.encoding or 'ascii', errors='replace'))
            else:
                safe_args.append(a)
        try:
            print(*safe_args, **kwargs)
        except Exception:
            print(*(str(a).encode('ascii', errors='replace').decode('ascii') for a in args), **kwargs)


def cmd_validate_config(args):
    """校验 workflow 配置文件。"""
    from .config.loader import load_workflow
    try:
        wf = load_workflow(args.workflow)
        safe_print(f"[OK] 配置校验通过: {wf.name} ({len(wf.states)} states, {len(wf.tasks)} tasks)")
        return 0
    except Exception as e:
        safe_print(f"[FAIL] 配置校验失败: {e}")
        return 1


def cmd_validate_state_machine(args):
    """校验状态机完备性。"""
    from .config.loader import load_workflow
    from .state_machine.machine import StateMachine
    try:
        wf = load_workflow(args.workflow)
        sm = StateMachine(wf)
        issues = sm.validate()
        if issues:
            for issue in issues:
                safe_print(f"[WARN]  {issue}")
            return 1
        safe_print(f"[OK] 状态机校验通过: {len(sm.states)} states, {len(sm.terminal_states)} terminal")
        return 0
    except Exception as e:
        safe_print(f"[FAIL] 校验失败: {e}")
        return 1


def cmd_smoke(args):
    """单个 Agent/Role 冒烟测试。"""
    from .agents.registry import AgentRegistry
    from .config.loader import load_agents_config

    agents_config = load_agents_config(args.agents) if args.agents else {}
    registry = AgentRegistry(agents_config)

    target = args.agent or args.role
    if not target:
        safe_print(f"[FAIL] 需要指定 --agent 或 --role")
        return 1

    safe_print(f"[*] 冒烟测试: {target}")
    try:
        adapter = registry.resolve(target)
        result = adapter.smoke_test()
        if result:
            safe_print(f"[OK] 冒烟通过: {target}")
            return 0
        else:
            safe_print(f"[FAIL] 冒烟失败: {target}")
            return 1
    except Exception as e:
        safe_print(f"[FAIL] 冒烟异常: {e}")
        return 1


def _discover_roles_and_agents(args):
    """P0e: 自动发现 workflow 同目录下的 roles.yaml / agents.yaml。

    返回 (roles, agents_dict, skills_dir)。
    CLI 参数覆盖自动发现。
    """
    import os

    wf_dir = os.path.dirname(os.path.abspath(args.workflow))
    agents_dict = {}
    skills_dir = None

    # 加载 roles（如果存在或通过 CLI 指定）
    roles_path = getattr(args, 'roles', None)
    if not roles_path:
        auto_roles = os.path.join(wf_dir, "roles.yaml")
        if os.path.exists(auto_roles):
            roles_path = auto_roles

    if roles_path and os.path.exists(roles_path):
        try:
            from .config.loader import load_roles_config
            roles_config = load_roles_config(roles_path)
            # 将 roles 合并到 workflow.roles（在 Runner 中使用）
            has_roles = True
        except Exception:
            import sys
            safe_print(f"[WARN] 加载 roles 配置失败: {roles_path}")
            roles_config = {}
            has_roles = False
    else:
        roles_config = {}
        has_roles = False

    # 加载 agents
    agents_path = getattr(args, 'agents', None)
    if not agents_path:
        auto_agents = os.path.join(wf_dir, "agents.yaml")
        if os.path.exists(auto_agents):
            agents_path = auto_agents

    if agents_path and os.path.exists(agents_path):
        try:
            from .config.loader import load_agents_config
            agents_dict = load_agents_config(agents_path)
        except Exception:
            import sys
            safe_print(f"[WARN] 加载 agents 配置失败: {agents_path}")

    # 自动发现 skills 目录
    auto_skills = os.path.join(wf_dir, "skills")
    if os.path.isdir(auto_skills):
        skills_dir = auto_skills

    return roles_config, agents_dict, skills_dir


def cmd_run(args):
    """启动 workflow。"""
    from .config.loader import load_workflow
    from .state_machine.runner import Runner

    wf = load_workflow(args.workflow)

    # P0e: 自动发现并加载 roles/agents
    roles_config, agents_dict, skills_dir = _discover_roles_and_agents(args)

    # 合并 roles 到 workflow（如果 workflow 中未内嵌 roles）
    if roles_config:
        for name, role in roles_config.items():
            if name not in wf.roles:
                wf.roles[name] = role

    runner = Runner(
        wf,
        goal=args.goal,
        project_root=args.project_root or ".",
        agents=agents_dict if agents_dict else None,
        skills_dir=getattr(args, 'skills_dir', None) or skills_dir,
    )
    run_id = runner.start()
    safe_print(f"\n[START] Workflow 启动: {run_id}")
    final_state = runner.run()

    # CLI 退出码：done → 0，failed → 1，cancelled → 2
    if runner._cancelled:
        safe_print(f"\n[STOP] Workflow 已取消: {run_id}")
        return 2
    elif final_state == "failed":
        safe_print(f"\n[FAIL] Workflow 失败: {run_id}")
        return 1
    else:
        safe_print(f"\n[OK] Workflow 完成: {run_id}")
        return 0


def cmd_status(args):
    """查看运行状态。"""
    from .observability.status import get_status
    status = get_status(args.run_id)
    safe_print(status)
    return 0


def cmd_explain(args):
    """解释当前状态。"""
    from .observability.explain import get_explanation
    explanation = get_explanation(args.run_id)
    safe_print(explanation)
    return 0


def cmd_log(args):
    """查看运行日志。"""
    from .observability.jsonl_sink import read_log
    if args.summary:
        summary = read_log(args.run_id, summary=True)
        safe_print(summary)
    else:
        events = read_log(args.run_id)
        for event in events:
            safe_print(event)
    return 0


def cmd_tail(args):
    """查看节点日志。"""
    from .observability.jsonl_sink import read_tail
    lines = read_tail(args.run_id, state=args.state, lines=args.lines)
    for line in lines:
        safe_print(line)
    return 0


def cmd_retry(args):
    """重试（默认 dry-run）。"""
    dispatch = args.dispatch
    from_state = args.from_state
    dry_run = not dispatch

    if dry_run:
        safe_print(f"[*] Dry-run 重试预览: run={args.run_id}, from={from_state or 'last failed'}")
    else:
        safe_print(f"[FIX] 执行重试: run={args.run_id}, from={from_state or 'last failed'}")

    from .state_machine.retry import retry_run
    result = retry_run(args.run_id, from_state=from_state, dry_run=dry_run)
    if result.get("ok"):
        safe_print(f"[OK] 重试预览完成" if dry_run else f"[OK] 重试完成")
    else:
        safe_print(f"[FAIL] 重试失败: {result.get('error', 'unknown')}")
    return 0 if result.get("ok") else 1


def cmd_cancel(args):
    """取消运行。支持 cross-cwd 取消。"""
    from .state_machine.runner import cancel_run
    ok = cancel_run(
        args.run_id,
        reason=args.reason or "",
        project_root=getattr(args, 'project_root', None) or None,
        run_root=getattr(args, 'run_root', None) or None,
    )
    if ok:
        safe_print(f"[STOP] 已取消: {args.run_id}")
        return 0
    else:
        safe_print(f"[FAIL] 取消失败: {args.run_id}")
        return 1


def build_parser():
    parser = argparse.ArgumentParser(
        prog="agent-workflow",
        description="Agent Workflow Core — 通用 Agent 编排引擎",
    )
    sub = parser.add_subparsers(dest="command", help="可用命令")

    # validate-config
    p = sub.add_parser("validate-config", help="校验配置文件")
    p.add_argument("--workflow", "-w", required=True, help="workflow YAML 路径")
    p.set_defaults(func=cmd_validate_config)

    # validate-state-machine
    p = sub.add_parser("validate-state-machine", help="校验状态机完备性")
    p.add_argument("--workflow", "-w", required=True, help="workflow YAML 路径")
    p.set_defaults(func=cmd_validate_state_machine)

    # smoke
    p = sub.add_parser("smoke", help="Agent/Role 冒烟测试")
    p.add_argument("--agent", help="Agent 名称")
    p.add_argument("--role", help="Role 名称")
    p.add_argument("--agents", help="agents YAML 路径")
    p.set_defaults(func=cmd_smoke)

    # run
    p = sub.add_parser("run", help="启动 workflow")
    p.add_argument("--workflow", "-w", required=True, help="workflow YAML 路径")
    p.add_argument("--goal", "-g", required=True, help="Workflow 目标描述")
    p.add_argument("--project-root", "-p", help="项目根目录（默认当前目录）")
    p.add_argument("--roles", help="roles YAML 路径（默认自动发现 workflow 同目录下的 roles.yaml）")
    p.add_argument("--agents", help="agents YAML 路径（默认自动发现 workflow 同目录下的 agents.yaml）")
    p.add_argument("--skills-dir", help="skills 目录（默认自动发现 workflow 同目录下的 skills/）")
    p.set_defaults(func=cmd_run)

    # status
    p = sub.add_parser("status", help="查看运行状态")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.set_defaults(func=cmd_status)

    # explain
    p = sub.add_parser("explain", help="解释当前状态")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.set_defaults(func=cmd_explain)

    # log
    p = sub.add_parser("log", help="查看运行日志")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--summary", "-s", action="store_true", help="仅输出摘要")
    p.set_defaults(func=cmd_log)

    # tail
    p = sub.add_parser("tail", help="查看节点日志")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--state", "-s", required=True, help="State 名称")
    p.add_argument("--lines", "-n", type=int, default=80, help="行数（默认 80）")
    p.set_defaults(func=cmd_tail)

    # retry
    p = sub.add_parser("retry", help="重试（默认 dry-run）")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--from-state", help="从指定 state 重试")
    p.add_argument("--dispatch", action="store_true", help="真实执行（非 dry-run）")
    p.set_defaults(func=cmd_retry)

    # cancel
    p = sub.add_parser("cancel", help="取消运行")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--reason", help="取消原因")
    p.add_argument("--project-root", help="项目根目录（用于 run_index.json 查找 run_root）")
    p.add_argument("--run-root", help="直接指定 run 目录")
    p.set_defaults(func=cmd_cancel)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
