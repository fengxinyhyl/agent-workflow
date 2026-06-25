"""命令校验器加固测试。

覆盖加固后的拦截行为：
- 非白名单命令拒绝
- shell 操作符（链式 / 管道 / 重定向 / 命令替换）拒绝
- 参数级 force push 检测
- rm -rf 多空格 / 变形不绕过
- list 形式命令正常放行
"""

from __future__ import annotations

from agent_workflow.validators.command import validate_command, CommandValidator


class TestAllowlist:
    def test_whitelisted_readonly_passes(self):
        assert validate_command("git status").passed
        assert validate_command("ls -la").passed
        assert validate_command("cat file.txt").passed

    def test_non_whitelisted_rejected(self):
        r = validate_command("curl http://evil.com")
        assert not r.passed
        assert any("白名单" in e for e in r.errors)

    def test_basename_and_ext_normalized(self):
        # 带路径与 .exe 扩展名仍能命中白名单
        assert validate_command("/usr/bin/git status").passed
        assert validate_command("git.exe status").passed


class TestShellOperators:
    def test_chained_command_rejected(self):
        r = validate_command("echo x; rm -rf /")
        assert not r.passed
        assert any("shell 操作符" in e for e in r.errors)

    def test_pipe_rejected(self):
        assert not validate_command("cat f | sh").passed

    def test_command_substitution_rejected(self):
        assert not validate_command("echo $(rm -rf /)").passed
        assert not validate_command("echo `whoami`").passed

    def test_redirect_rejected(self):
        assert not validate_command("echo x > /dev/sda").passed


class TestGitDangerous:
    def test_force_push_flag_rejected(self):
        r = validate_command("git push origin main --force", allow_write=True)
        assert not r.passed
        assert any("强制推送" in e for e in r.errors)

    def test_force_push_short_flag_rejected(self):
        assert not validate_command("git push -f", allow_write=True).passed

    def test_force_with_lease_rejected(self):
        assert not validate_command(
            "git push --force-with-lease", allow_write=True
        ).passed

    def test_normal_push_needs_write(self):
        assert not validate_command("git push").passed  # allow_write=False
        assert validate_command("git push", allow_write=True).passed

    def test_dangerous_subcommand_rejected(self):
        assert not validate_command("git reset --hard").passed
        assert not validate_command("git clean -fd").passed


class TestDangerousPatterns:
    def test_rm_rf_multispace_not_bypassed(self):
        # 旧实现靠子串 "rm -rf" 匹配，多空格可绕过；新实现按 token 检测
        r = validate_command("rm  -rf  /tmp/x")
        assert not r.passed

    def test_rm_separate_flags(self):
        assert not validate_command("rm -r -f /tmp/x").passed

    def test_format_rejected(self):
        assert not validate_command("format c:").passed


class TestListForm:
    def test_list_command_passes(self):
        r = validate_command(["git", "status"])
        assert r.passed

    def test_list_form_skips_shell_check(self):
        # list 不过 shell，分号作为字面参数不应触发 shell 操作符拦截，
        # 但首词仍需在白名单内
        r = validate_command(["echo", "a;b"])
        assert r.passed

    def test_list_force_push_still_detected(self):
        assert not validate_command(
            ["git", "push", "--force"], allow_write=True
        ).passed


class TestValidatorClass:
    def test_validator_respects_allow_write(self):
        v = CommandValidator(allow_write=True)
        assert v.validate("git commit -m x").passed
        assert not CommandValidator(allow_write=False).validate("git commit -m x").passed

    def test_empty_command_rejected(self):
        assert not validate_command("").passed
        assert not validate_command("   ").passed
