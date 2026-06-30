"""测试 Artifact Staging / Promotion。"""

import os
import tempfile
import pytest

from agent_workflow.artifacts import (
    ensure_staging_dir,
    get_staging_path,
    promote_artifact,
    validate_and_promote,
)


class TestStaging:
    """Staging 目录测试。"""

    def test_ensure_staging_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            staging_dir = ensure_staging_dir(run_root, "codex_plan")
            assert os.path.exists(staging_dir)
            assert "codex_plan" in staging_dir

    def test_get_staging_path(self):
        path = get_staging_path("/tmp/runs/run_001", "codex_plan", "output.md")
        assert "staging" in path
        assert "codex_plan" in path
        assert "output.md" in path


class TestPromotion:
    """Promotion 测试。"""

    def test_promote_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            staging_dir = ensure_staging_dir(run_root, "codex_plan")

            # 创建 staging 文件
            staging_file = os.path.join(staging_dir, "output.md")
            with open(staging_file, "w") as f:
                f.write("# Test Output\n\nTest content.")

            # Promote
            artifact_path = os.path.join(run_root, "artifacts", "output.md")
            result = promote_artifact(
                staging_path=staging_file,
                artifact_path=artifact_path,
                run_root=run_root,
                artifact_name="output",
            )

            assert result.ok
            assert os.path.exists(artifact_path)

            # staging 保留
            assert os.path.exists(staging_file)

    def test_promote_missing_staging(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            # staging_path 指向 run_root/staging 下的不存在的文件
            staging_path = os.path.join(run_root, "staging", "missing_state", "missing.md")
            artifact_path = os.path.join(run_root, "artifacts", "output.md")
            result = promote_artifact(
                staging_path=staging_path,
                artifact_path=artifact_path,
                run_root=run_root,
                artifact_name="output",
            )
            assert not result.ok
            assert "不存在" in result.error

    def test_validate_and_promote_with_validator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            staging_dir = ensure_staging_dir(run_root, "codex_plan")

            staging_file = os.path.join(staging_dir, "output.md")
            with open(staging_file, "w") as f:
                f.write("test")

            def passing_validator(path):
                return True

            result = validate_and_promote(
                run_root=run_root,
                state_name="codex_plan",
                staging_filename="output.md",
                artifact_name="output",
                validator=passing_validator,
            )
            assert result.ok

    def test_validate_and_promote_validator_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_001")
            staging_dir = ensure_staging_dir(run_root, "codex_plan")

            staging_file = os.path.join(staging_dir, "output.md")
            with open(staging_file, "w") as f:
                f.write("test")

            def failing_validator(path):
                return False

            result = validate_and_promote(
                run_root=run_root,
                state_name="codex_plan",
                staging_filename="output.md",
                artifact_name="output",
                validator=failing_validator,
            )
            assert not result.ok


class TestWorktreeStaging:
    """worktree 模式下 staging_root（agent 沙箱）≠ run_root（主仓）。

    回归：早先 promotion 用 run_root 拼相对 staging_path，worktree 下导致
    路径重复且跨树找不到文件。
    """

    def test_promote_with_separate_staging_root(self):
        """staging 在 project_root 树、artifacts 在 run_root 树，promote 成功。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = os.path.join(tmpdir, "worktree", "sp1")
            run_root = os.path.join(tmpdir, "mainrepo", "docs", "runs", "run_001")

            # agent 在 project_root 沙箱里写 staging
            staging_dir = os.path.join(project_root, "staging", "execution")
            os.makedirs(staging_dir, exist_ok=True)
            staging_file = os.path.join(staging_dir, "execution_report.md")
            with open(staging_file, "w", encoding="utf-8") as f:
                f.write("report content")

            artifact_path = os.path.join(run_root, "artifacts", "execution_report.md")
            result = promote_artifact(
                staging_path=staging_file,
                artifact_path=artifact_path,
                run_root=run_root,
                artifact_name="execution_report",
                staging_root=project_root,
            )
            assert result.ok, result.error
            # artifact promote 到主仓 run_root，恢复能力不受影响
            assert os.path.exists(artifact_path)
            # staging 保留在 worktree
            assert os.path.exists(staging_file)

    def test_relative_staging_resolves_against_staging_root(self):
        """相对 staging_path 应相对 staging_root 解析，而非 run_root。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = os.path.join(tmpdir, "worktree", "sp1")
            run_root = os.path.join(tmpdir, "mainrepo", "docs", "runs", "run_001")

            staging_dir = os.path.join(project_root, "staging", "execution")
            os.makedirs(staging_dir, exist_ok=True)
            with open(os.path.join(staging_dir, "report.md"), "w", encoding="utf-8") as f:
                f.write("x")

            result = promote_artifact(
                staging_path=os.path.join("staging", "execution", "report.md"),
                artifact_path=os.path.join(run_root, "artifacts", "report.md"),
                run_root=run_root,
                artifact_name="report",
                staging_root=project_root,
            )
            assert result.ok, result.error

    def test_staging_outside_sandbox_rejected(self):
        """staging 文件在沙箱外（无 staging 段）→ 拒绝。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = os.path.join(tmpdir, "worktree", "sp1")
            run_root = os.path.join(tmpdir, "mainrepo", "docs", "runs", "run_001")
            os.makedirs(project_root, exist_ok=True)

            # 落在沙箱内但不经 staging/ 段 → 必须拒绝（防登记任意源码）
            src_file = os.path.join(project_root, "src", "main.py")
            os.makedirs(os.path.dirname(src_file), exist_ok=True)
            with open(src_file, "w", encoding="utf-8") as f:
                f.write("code")

            result = promote_artifact(
                staging_path=src_file,
                artifact_path=os.path.join(run_root, "artifacts", "main.py"),
                run_root=run_root,
                artifact_name="main",
                staging_root=project_root,
            )
            assert not result.ok
            assert "逃逸" in result.error

    def test_check_staging_sandbox_helper(self):
        """_check_staging_sandbox：多根 + staging 段约束。"""
        from agent_workflow.artifacts.promotion import _check_staging_sandbox

        roots = ["/tmp/worktree", "/tmp/mainrepo/runs/r1"]
        # 在第一个根的 staging 下 → 通过
        assert _check_staging_sandbox("/tmp/worktree/staging/s/f.md", roots)
        # 在第二个根的 staging 下 → 通过
        assert _check_staging_sandbox("/tmp/mainrepo/runs/r1/staging/s/f.md", roots)
        # 在根内但无 staging 段 → 拒绝
        assert not _check_staging_sandbox("/tmp/worktree/src/main.py", roots)
        # 在所有根外 → 拒绝
        assert not _check_staging_sandbox("/tmp/elsewhere/staging/f.md", roots)
        # .. 穿越逃逸 → 拒绝
        assert not _check_staging_sandbox("/tmp/worktree/staging/../../escape/staging/f.md", roots)

