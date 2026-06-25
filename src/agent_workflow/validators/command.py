"""CommandValidator — 校验 CLI 命令安全性。

P0 命令 allowlist:
- 文件读取: cat, head, tail, grep, rg, find, ls, dir, wc
- Git 操作: git status, git log, git diff, git branch, git show, git stash list
- 环境信息: python --version, pip list, node --version, which, where, pwd, echo
- 写入操作: git add, git commit, git checkout -b, git stash, mkdir

加固策略（相对 P0）:
- 非白名单命令、危险模式、shell 操作符一律 add_error（拦截），不再仅警告
- 用 shlex 做词法解析，避免简单 split 漏判带引号/转义的命令
- 字符串形式禁止 shell 元字符（; | & $() ` 重定向 换行），防止链式命令绕过
- git push 的 --force/-f 等危险参数做参数级检测，不再只看子命令名
"""

from __future__ import annotations

import os
import shlex

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

# 需要写入权限的 Git 子命令
GIT_WRITE_SUBCOMMANDS: set[str] = {"add", "commit", "checkout", "push", "stash"}

# Shell 元字符 / 操作符：出现在字符串命令中即拒绝（防止链式命令与重定向绕过）。
# 注意 list 形式的命令不经过 shell，天然安全，不做此检查。
SHELL_OPERATORS: tuple[str, ...] = (
    "&&", "||", ";", "|", "&", "$(", "`", ">>", ">", "<", "\n", "\r",
)

# 参数级危险标志（按 token 精确匹配，避免 "rm  -rf" 多空格绕过子串匹配）
GIT_PUSH_DANGEROUS_FLAGS: set[str] = {"--force", "-f", "--force-with-lease"}


def _normalize_base_cmd(token: str) -> str:
    """归一化命令名：取 basename 并去掉 Windows 可执行扩展名。"""
    base = os.path.basename(token.strip()).lower()
    for ext in (".exe", ".cmd", ".bat", ".com"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    return base


def validate_command(
    command: str | list[str] | tuple[str, ...],
    allow_write: bool = False,
) -> ValidationResult:
    """校验 CLI 命令安全性。

    策略:
    - 默认仅允许白名单内的只读命令，非白名单一律拒绝
    - git add/commit/checkout/push/stash 需要 allow_write=True，否则拒绝
    - git reset/clean/force push 等危险操作永远拒绝
    - 字符串命令禁止 shell 元字符（链式、管道、重定向、命令替换）

    Args:
        command: 命令字符串，或已分词的命令 list（list 形式不过 shell，更安全）
        allow_write: 是否允许写入类命令
    """
    result = ValidationResult()

    # 归一化为 token 列表
    if isinstance(command, (list, tuple)):
        tokens = [str(p) for p in command]
        raw = " ".join(tokens)
        from_string = False
    else:
        raw = (command or "").strip()
        if not raw:
            result.add_error("命令为空")
            return result
        from_string = True

        # 字符串形式：先拦截 shell 元字符（链式 / 管道 / 重定向 / 命令替换）
        for op in SHELL_OPERATORS:
            if op in raw:
                result.add_error(
                    f"命令包含 shell 操作符 '{op}'，禁止链式 / 管道 / 重定向命令"
                )
                result.metadata["command"] = raw
                return result

        # 词法解析（兼容带引号 / 转义的命令；posix=False 以贴近 Windows 行为）
        try:
            tokens = shlex.split(raw, posix=False)
        except ValueError as e:
            result.add_error(f"命令无法解析（引号 / 转义不匹配）: {e}")
            result.metadata["command"] = raw
            return result

    if not tokens:
        result.add_error("命令为空")
        return result

    base_cmd = _normalize_base_cmd(tokens[0])

    # 1. 基础命令必须在白名单内，否则拒绝
    if base_cmd not in COMMAND_ALLOWLIST:
        result.add_error(f"命令 '{base_cmd}' 不在白名单中，已拒绝")

    # 2. git 子命令检查
    if base_cmd == "git" and len(tokens) > 1:
        sub_cmd = tokens[1].lower()

        if sub_cmd in GIT_DANGEROUS_SUBCOMMANDS:
            result.add_error(f"Git 子命令 '{sub_cmd}' 被禁止（危险操作）")
            result.metadata["command"] = raw
            return result

        # 参数级危险检测：force push 不再只看子命令名
        if sub_cmd == "push":
            for tok in tokens[2:]:
                flag = tok.lower()
                if flag in GIT_PUSH_DANGEROUS_FLAGS or flag.startswith("--force"):
                    result.add_error(f"Git push 禁止强制推送参数 '{tok}'")
                    result.metadata["command"] = raw
                    return result

        if sub_cmd in GIT_WRITE_SUBCOMMANDS and not allow_write:
            result.add_error(
                f"Git 子命令 '{sub_cmd}' 需要写入权限，当前 allow_write=False"
            )

        if sub_cmd not in GIT_SAFE_SUBCOMMANDS and sub_cmd not in GIT_DANGEROUS_SUBCOMMANDS:
            result.add_warning(f"Git 子命令 '{sub_cmd}' 不在安全列表中")

    # 3. 危险模式检测（token 级精确匹配 + 原始串兜底）
    lower_tokens = [t.lower() for t in tokens]

    # rm / del 带递归或强制标志
    if base_cmd in ("rm", "del"):
        for tok in lower_tokens[1:]:
            if tok.startswith("-") and ("r" in tok or "f" in tok):
                result.add_error(f"危险删除参数: '{tok}'")
                break

    # 整命令级危险关键字（覆盖未走到上面分支的情况）
    dangerous_keywords = {
        "format", "mkfs", "shutdown", "reboot", "dd",
    }
    if base_cmd in dangerous_keywords:
        result.add_error(f"命令 '{base_cmd}' 属于危险操作，已拒绝")

    result.metadata["command"] = raw
    result.metadata["base_cmd"] = base_cmd
    result.metadata["allow_write"] = allow_write
    return result


class CommandValidator(BaseValidator):
    """命令校验器。"""

    name = "command"

    def __init__(self, allow_write: bool = False):
        self.allow_write = allow_write

    def validate(self, command: str | list[str] | tuple[str, ...]) -> ValidationResult:
        """校验命令。"""
        return validate_command(command, allow_write=self.allow_write)
