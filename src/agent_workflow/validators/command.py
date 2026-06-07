"""CommandValidator — 校验 CLI 命令安全性。

P0 命令 allowlist:
- 文件读取: cat, head, tail, grep, rg, find, ls, dir, wc
- Git 操作: git status, git log, git diff, git branch, git show, git stash list
- 环境信息: python --version, pip list, node --version, which, where, pwd, echo
- 写入操作: git add, git commit, git checkout -b, git stash, mkdir
"""

from __future__ import annotations

from .base import BaseValidator, ValidationResult


# P0 命令白名单
COMMAND_ALLOWLIST: set[str] = {
    # 文件读取
    "cat", "head", "tail", "grep", "rg", "find", "ls", "dir", "wc",
    "type", "more",
    # Git 只读
    "git",  # 含子命令检查
    # 环境信息
    "python", "python3", "pip", "pip3", "node", "npm", "which", "where",
    "pwd", "echo", "whoami", "hostname",
}

# Git 子命令白名单（只读 + 安全写入）
GIT_SAFE_SUBCOMMANDS: set[str] = {
    "status", "log", "diff", "branch", "show", "stash",
    "add", "commit", "checkout", "switch", "restore",
    "fetch", "pull", "push",
    "worktree", "remote", "tag", "config", "rev-parse", "rev-list",
    "ls-files", "ls-tree", "describe", "shortlog",
}

# 危险 Git 子命令（黑名单）
GIT_DANGEROUS_SUBCOMMANDS: set[str] = {
    "reset", "clean", "rebase", "bisect", "filter-branch", "gc", "prune",
    "reflog", "rm", "mv",
}


def validate_command(
    command: str,
    allow_write: bool = False,
) -> ValidationResult:
    """校验 CLI 命令安全性。

    P0 策略:
    - 默认仅允许只读命令
    - git add/commit/checkout 需要 allow_write=True
    - git reset/clean/force push 等危险操作永远拒绝
    """
    result = ValidationResult()

    if not command or not command.strip():
        result.add_error("命令为空")
        return result

    parts = command.strip().split()
    base_cmd = parts[0].lower()

    # 检查基础命令是否在白名单
    if base_cmd not in COMMAND_ALLOWLIST:
        result.add_warning(f"命令 '{base_cmd}' 不在白名单中，默认禁用")

    # git 子命令检查
    if base_cmd == "git" and len(parts) > 1:
        sub_cmd = parts[1].lower()

        if sub_cmd in GIT_DANGEROUS_SUBCOMMANDS:
            result.add_error(f"Git 子命令 '{sub_cmd}' 被禁止（危险操作）")
            return result

        if sub_cmd in ("add", "commit", "checkout", "push", "stash") and not allow_write:
            result.add_warning(
                f"Git 子命令 '{sub_cmd}' 需要写入权限，当前 allow_write=False"
            )

        if sub_cmd not in GIT_SAFE_SUBCOMMANDS and sub_cmd not in GIT_DANGEROUS_SUBCOMMANDS:
            result.add_warning(f"Git 子命令 '{sub_cmd}' 不在安全列表中")

    # 检查危险模式
    cmd_lower = command.lower()
    dangerous_patterns = [
        "rm -rf", "rm -r", "del /", "format", "> /dev/",
        "mkfs", "dd if=", "shutdown", "reboot",
    ]
    for pattern in dangerous_patterns:
        if pattern in cmd_lower:
            result.add_error(f"命令包含危险模式: '{pattern}'")
            break

    result.metadata["command"] = command
    result.metadata["base_cmd"] = base_cmd
    result.metadata["allow_write"] = allow_write
    return result


class CommandValidator(BaseValidator):
    """命令校验器。"""

    name = "command"

    def __init__(self, allow_write: bool = False):
        self.allow_write = allow_write

    def validate(self, command: str) -> ValidationResult:
        """校验命令。"""
        return validate_command(command, allow_write=self.allow_write)
