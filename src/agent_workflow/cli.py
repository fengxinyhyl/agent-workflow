"""agent-workflow CLI 入口。

P0 CLI 命令：
  validate-config       校验配置文件
  validate-state-machine  校验状态机完备性
  smoke                 单个 Agent 冒烟测试
  run                   启动 workflow
  status                查看运行状态
  explain               解释当前等待项和可能的后续状态
  log                   查看运行日志
  tail                  查看节点日志
  retry                 重试（默认 dry-run）
  continue              从 Gate 暂停状态恢复
  cancel                取消运行
"""

import argparse
import json
import os
import sys


def _find_run_root(run_id: str, project_root: str | None = None, run_root_hint: str | None = None) -> str | None:
    """根据 run_id 发现 run_root。

    发现优先级：
    1. run_root_hint 显式指定（如 --run-root doc/ → {project_root}/doc/{run_id}/）
    2. project_root + run_index.json 查找
    3. cwd + run_index.json 查找
    4. cwd + .agent-workflow/runs/<run_id>/

    返回 run_root 绝对路径，找不到则返回 None。
    """
    # 决定相对路径解析的基准
    resolve_base = os.path.abspath(project_root) if project_root else os.path.abspath(".")

    # 1. 显式指定 run_root_hint
    if run_root_hint:
        # 如果是绝对路径直接使用，否则基于 project_root 解析
        if os.path.isabs(run_root_hint):
            base = os.path.abspath(run_root_hint)
        else:
            base = os.path.abspath(os.path.join(resolve_base, run_root_hint))
        # 如果 hint 已经是具体的 run 目录（以 run_id 结尾）
        if os.path.basename(base) == run_id:
            return base if os.path.exists(base) else None
        # 否则视为 base_run_root
        candidate = os.path.join(base, run_id)
        return candidate if os.path.exists(candidate) else None

    # 2-3. run_index.json 查找
    search_roots = []
    if project_root:
        search_roots.append(os.path.abspath(project_root))
    search_roots.append(os.path.abspath("."))

    for root in search_roots:
        index_path = os.path.join(root, "doc", "run_index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
                if run_id in index and os.path.exists(index[run_id]):
                    return index[run_id]
            except (json.JSONDecodeError, IOError):
                pass

    # 4. 默认路径
    for root in search_roots:
        default = os.path.join(root, "doc", "runs", run_id)
        if os.path.exists(default):
            return default

    return None


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
    """单个 Agent 冒烟测试。"""
    from .agents.registry import AgentRegistry
    from .config.loader import load_agents_config

    agents_config = load_agents_config(args.agents) if args.agents else {}
    registry = AgentRegistry(agents_config)

    target = args.agent
    if not target:
        safe_print(f"[FAIL] 需要指定 --agent")
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


def _discover_agents(args):
    """P0e: 自动发现 workflow 同目录下的 agents.yaml。

    返回 (agents_dict, skills_dir, mock_script)。
    CLI 参数覆盖自动发现。
    """
    import os

    wf_dir = os.path.dirname(os.path.abspath(args.workflow))
    agents_dict = {}
    skills_dir = None

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

    # 自动发现 mock_script.yaml（mock 模式下演示状态机回流分支用）
    mock_script = {}
    mock_script_path = getattr(args, 'mock_script', None)
    if not mock_script_path:
        auto_mock = os.path.join(wf_dir, "mock_script.yaml")
        if os.path.exists(auto_mock):
            mock_script_path = auto_mock
    if mock_script_path and os.path.exists(mock_script_path):
        try:
            import yaml
            with open(mock_script_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # 支持顶层 decision_script 键或直接是 state→list 映射
            mock_script = data.get("decision_script", data) if isinstance(data, dict) else {}
        except Exception:
            safe_print(f"[WARN] 加载 mock_script 失败: {mock_script_path}")

    return agents_dict, skills_dir, mock_script


def _parse_agent_map(raw: str) -> dict[str, str]:
    """解析 --agent-map 字符串，返回 {key: agent_name}。

    key 格式必须为 "state:<name>" 或 "task:<name>"。
    格式错误时抛出 ValueError（fail-fast）。
    """
    if not raw:
        return {}
    result: dict[str, str] = {}
    for part in raw.split(','):
        part = part.strip()
        if '=' not in part:
            raise ValueError(f"--agent-map 格式错误（缺少 '='）：{part!r}")
        key, value = part.split('=', 1)
        key, value = key.strip(), value.strip()
        if not key:
            raise ValueError(f"--agent-map key 不能为空：{part!r}")
        if not value:
            raise ValueError(f"--agent-map value 不能为空：{part!r}")
        if not (key.startswith('state:') or key.startswith('task:')):
            raise ValueError(f"--agent-map key 必须以 'state:' 或 'task:' 开头：{key!r}")
        if key in result:
            raise ValueError(f"--agent-map 包含重复 key：{key!r}")
        result[key] = value
    return result


def _validate_agent_overrides(
    agent_overrides: dict[str, str],
    workflow,
    agents_dict: dict,
) -> None:
    """校验 agent overrides 中的 state/task 存在于 workflow，agent 已注册。"""
    valid_states = set(workflow.states.keys())
    valid_tasks = set(workflow.tasks.keys())
    for key, agent_name in agent_overrides.items():
        if key.startswith('state:'):
            name = key[len('state:'):]
            if name not in valid_states:
                raise ValueError(f"--agent-map 引用了不存在的 state：{name!r}")
        elif key.startswith('task:'):
            name = key[len('task:'):]
            if name not in valid_tasks:
                raise ValueError(f"--agent-map 引用了不存在的 task：{name!r}")
        if agent_name != 'mock' and agents_dict and agent_name not in agents_dict:
            raise ValueError(f"--agent-map 引用了未注册的 agent：{agent_name!r}（请检查 agents.yaml）")


def cmd_run(args):
    """启动 workflow。"""
    from .config.loader import load_workflow
    from .state_machine.runner import Runner

    wf = load_workflow(args.workflow)

    # P0e: 自动发现并加载 agents
    agents_dict, skills_dir, mock_script = _discover_agents(args)

    # 解析并校验 --agent-map
    try:
        agent_overrides = _parse_agent_map(getattr(args, 'agent_map', '') or '')
    except ValueError as e:
        safe_print(f"[FAIL] {e}", file=sys.stderr)
        return 1

    if agent_overrides:
        try:
            _validate_agent_overrides(agent_overrides, wf, agents_dict)
        except ValueError as e:
            safe_print(f"[FAIL] {e}", file=sys.stderr)
            return 1

    runner = Runner(
        wf,
        goal=args.goal,
        topic=getattr(args, 'topic', '') or '',
        project_root=args.project_root or ".",
        run_root=getattr(args, 'run_root', None) or None,
        agents=agents_dict if agents_dict else None,
        skills_dir=getattr(args, 'skills_dir', None) or skills_dir,
        mock_script=mock_script,
        agent_overrides=agent_overrides or None,
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
    run_root = _find_run_root(
        args.run_id,
        project_root=getattr(args, 'project_root', None) or None,
        run_root_hint=getattr(args, 'run_root', None) or None,
    )
    if run_root is None:
        safe_print(f"[FAIL] 未找到运行: {args.run_id}")
        return 1
    status = get_status(args.run_id, run_root=run_root)
    safe_print(status)
    return 0


def cmd_explain(args):
    """解释当前状态。"""
    from .observability.explain import get_explanation
    run_root = _find_run_root(
        args.run_id,
        project_root=getattr(args, 'project_root', None) or None,
        run_root_hint=getattr(args, 'run_root', None) or None,
    )
    if run_root is None:
        safe_print(f"[FAIL] 未找到运行: {args.run_id}")
        return 1
    explanation = get_explanation(args.run_id, run_root=run_root)
    safe_print(explanation)
    return 0


def cmd_log(args):
    """查看运行日志。"""
    from .observability.jsonl_sink import read_log
    run_root = _find_run_root(
        args.run_id,
        project_root=getattr(args, 'project_root', None) or None,
        run_root_hint=getattr(args, 'run_root', None) or None,
    )
    if run_root is None:
        safe_print(f"[FAIL] 未找到运行: {args.run_id}")
        return 1
    if args.summary:
        summary = read_log(args.run_id, summary=True, run_root=run_root)
        safe_print(summary)
    else:
        events = read_log(args.run_id, run_root=run_root)
        for event in events:
            safe_print(event)
    return 0


def cmd_tail(args):
    """查看节点日志。"""
    from .observability.jsonl_sink import read_tail
    run_root = _find_run_root(
        args.run_id,
        project_root=getattr(args, 'project_root', None) or None,
        run_root_hint=getattr(args, 'run_root', None) or None,
    )
    if run_root is None:
        safe_print(f"[FAIL] 未找到运行: {args.run_id}")
        return 1
    lines = read_tail(args.run_id, state=args.state, lines=args.lines, run_root=run_root)
    for line in lines:
        safe_print(line)
    return 0


def cmd_retry(args):
    """重试（默认 dry-run）。"""
    dispatch = args.dispatch
    from_state = args.from_state
    dry_run = not dispatch

    run_root = _find_run_root(
        args.run_id,
        project_root=getattr(args, 'project_root', None) or None,
        run_root_hint=getattr(args, 'run_root', None) or None,
    )
    if run_root is None:
        safe_print(f"[FAIL] 未找到运行: {args.run_id}")
        return 1

    if dry_run:
        safe_print(f"[*] Dry-run 重试预览: run={args.run_id}, from={from_state or 'auto-detect'}")
    else:
        safe_print(f"[FIX] 执行重试: run={args.run_id}, from={from_state or 'auto-detect'}")

    # dispatch 模式下尝试自动发现 agents/skills
    agents_dict = None
    skills_dir = None
    if dispatch:
        workflow_path = getattr(args, 'workflow', None)
        if workflow_path and os.path.exists(workflow_path):
            safe_print(f"[*] 自动发现 agents/skills: {os.path.dirname(workflow_path)}")
            try:
                agents_dict, skills_dir, _ = _discover_agents(args)
            except Exception:
                safe_print("[WARN] 自动发现 agents/skills 失败，将使用 mock agent")

    from .state_machine.retry import retry_run
    result = retry_run(
        args.run_id,
        from_state=from_state,
        dry_run=dry_run,
        run_root=run_root,
        project_root=getattr(args, 'project_root', None) or ".",
        agents=agents_dict,
        skills_dir=skills_dir,
    )

    if result.get("ok"):
        if dry_run:
            safe_print(f"[OK] 重试预览完成")
            # 输出预览步骤
            for step in result.get("steps", []):
                safe_print(f"  - {step['action']}: {step['status']}")
                detail = step.get("detail", {})
                if isinstance(detail, dict):
                    for k, v in detail.items():
                        if k == "operations":
                            for op in v:
                                safe_print(f"      • {op}")
                        elif k == "next_states":
                            safe_print(f"      {k}: {v}")
        else:
            final = result.get("final_state", "?")
            safe_print(f"[OK] 重试完成 → 终态: {final}")
    else:
        safe_print(f"[FAIL] 重试失败: {result.get('error', 'unknown')}")
    return 0 if result.get("ok") else 1


def _copy_human_clarification(input_path: str, runner) -> str:
    """把人工澄清文件复制为 run artifact 并登记到 RunContext。"""
    import shutil

    if runner.context is None:
        raise RuntimeError("Runner context 未初始化")

    source = os.path.abspath(input_path)
    if not os.path.exists(source):
        raise FileNotFoundError(f"人工澄清输入不存在: {input_path}")

    artifacts_dir = os.path.join(runner.context.run_root, "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    target = os.path.join(artifacts_dir, "human_clarification.md")
    shutil.copy2(source, target)
    runner.context.promote_artifact("human_clarification", target)
    runner.context.save()
    return target


def cmd_continue(args):
    """从 Gate 暂停状态继续执行。"""
    from .config.loader import load_workflow
    from .state_machine.runner import Runner

    if args.approve == args.reject:
        safe_print("[FAIL] 必须且只能指定 --approve 或 --reject")
        return 1

    run_root = _find_run_root(
        args.run_id,
        project_root=getattr(args, 'project_root', None) or None,
        run_root_hint=getattr(args, 'run_root', None) or None,
    )
    if run_root is None:
        safe_print(f"[FAIL] 未找到运行: {args.run_id}")
        return 1

    if not args.workflow:
        safe_print("[FAIL] continue 需要 --workflow 以恢复状态机配置")
        return 1

    try:
        wf = load_workflow(args.workflow)
        agents_dict, skills_dir, mock_script = _discover_agents(args)
        runner = Runner.attach_existing(
            run_root,
            wf,
            project_root=getattr(args, 'project_root', None) or ".",
            agents=agents_dict if agents_dict else None,
            skills_dir=getattr(args, 'skills_dir', None) or skills_dir,
            mock_script=mock_script,
        )

        if args.input:
            artifact_path = _copy_human_clarification(args.input, runner)
            safe_print(f"[*] 已注入 human_clarification: {artifact_path}")

        final_state = runner.continue_from_gate(approved=args.approve)
        if final_state == "failed":
            safe_print(f"[FAIL] Workflow 继续后失败: {args.run_id}")
            return 1

        safe_print(f"[OK] Workflow 已继续: {args.run_id} -> {final_state}")
        return 0
    except Exception as e:
        safe_print(f"[FAIL] continue 失败: {e}")
        return 1


def cmd_cancel(args):
    """取消运行。支持 cross-cwd 取消。"""
    from .state_machine.runner import cancel_run
    run_root = _find_run_root(
        args.run_id,
        project_root=getattr(args, 'project_root', None) or None,
        run_root_hint=getattr(args, 'run_root', None) or None,
    )
    # cancel_run 内部会处理 run_root 为 None 的情况（写入默认路径）
    ok = cancel_run(
        args.run_id,
        reason=args.reason or "",
        project_root=getattr(args, 'project_root', None) or None,
        run_root=run_root or getattr(args, 'run_root', None) or None,
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
    p = sub.add_parser("smoke", help="Agent 冒烟测试")
    p.add_argument("--agent", help="Agent 名称")
    p.add_argument("--agents", help="agents YAML 路径")
    p.set_defaults(func=cmd_smoke)

    # run
    p = sub.add_parser("run", help="启动 workflow")
    p.add_argument("--workflow", "-w", required=True, help="workflow YAML 路径")
    p.add_argument("--goal", "-g", required=True, help="Workflow 目标描述")
    p.add_argument("--topic", "-t", default="",
                   help="任务名称（用于 run 目录命名，如 260612_project_create）")
    p.add_argument("--project-root", "-p", help="项目根目录（默认当前目录）")
    p.add_argument("--run-root", help="运行产物根目录（默认 {project_root}/doc/runs）")
    p.add_argument("--agents", help="agents YAML 路径（默认自动发现 workflow 同目录下的 agents.yaml）")
    p.add_argument("--skills-dir", help="skills 目录（默认自动发现 workflow 同目录下的 skills/）")
    p.add_argument("--mock-script", help="mock decision 脚本 YAML（默认自动发现 workflow 同目录下的 mock_script.yaml，仅 mock 模式生效）")
    p.add_argument("--agent-map", default="",
                   help="运行时 agent 覆盖，格式: state:s1=agent1,task:t1=agent2")
    p.set_defaults(func=cmd_run)

    # status
    p = sub.add_parser("status", help="查看运行状态")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--project-root", "-p", help="项目根目录（用于 run_index.json 发现）")
    p.add_argument("--run-root", help="run_root 路径（直接指定）")
    p.set_defaults(func=cmd_status)

    # explain
    p = sub.add_parser("explain", help="解释当前状态")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--project-root", "-p", help="项目根目录（用于 run_index.json 发现）")
    p.add_argument("--run-root", help="run_root 路径（直接指定）")
    p.set_defaults(func=cmd_explain)

    # log
    p = sub.add_parser("log", help="查看运行日志")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--summary", "-s", action="store_true", help="仅输出摘要")
    p.add_argument("--project-root", "-p", help="项目根目录（用于 run_index.json 发现）")
    p.add_argument("--run-root", help="run_root 路径（直接指定）")
    p.set_defaults(func=cmd_log)

    # tail
    p = sub.add_parser("tail", help="查看节点日志")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--state", "-s", required=True, help="State 名称")
    p.add_argument("--lines", "-n", type=int, default=80, help="行数（默认 80）")
    p.add_argument("--project-root", "-p", help="项目根目录（用于 run_index.json 发现）")
    p.add_argument("--run-root", help="run_root 路径（直接指定）")
    p.set_defaults(func=cmd_tail)

    # retry
    p = sub.add_parser("retry", help="重试（默认 dry-run）")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--from-state", help="从指定 state 重试（默认自动检测中断点）")
    p.add_argument("--dispatch", action="store_true", help="真实执行（非 dry-run）")
    p.add_argument("--workflow", "-w", help="workflow YAML 路径（dispatch 模式下用于自动发现 agents/skills）")
    p.add_argument("--project-root", "-p", help="项目根目录（用于 run_index.json 发现）")
    p.add_argument("--run-root", help="run_root 路径（直接指定）")
    p.set_defaults(func=cmd_retry)

    # continue
    p = sub.add_parser("continue", help="从 Gate 暂停状态继续执行")
    p.add_argument("--run-id", "-r", required=True, help="Run ID")
    p.add_argument("--workflow", "-w", required=True, help="workflow YAML 路径")
    p.add_argument("--approve", action="store_true", help="批准 gate 并继续")
    p.add_argument("--reject", action="store_true", help="拒绝 gate 并进入 failed")
    p.add_argument("--input", help="人工澄清 Markdown 文件，注入为 human_clarification artifact")
    p.add_argument("--project-root", "-p", help="项目根目录（用于 run_index.json 发现）")
    p.add_argument("--run-root", help="run_root 路径（直接指定）")
    p.add_argument("--agents", help="agents YAML 路径（默认自动发现 workflow 同目录下的 agents.yaml）")
    p.add_argument("--skills-dir", help="skills 目录（默认自动发现 workflow 同目录下的 skills/）")
    p.add_argument("--mock-script", help="mock decision 脚本 YAML（默认自动发现 workflow 同目录下的 mock_script.yaml，仅 mock 模式生效）")
    p.set_defaults(func=cmd_continue)

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
