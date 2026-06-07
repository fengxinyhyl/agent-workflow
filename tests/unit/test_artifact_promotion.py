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
        result = promote_artifact(
            staging_path="/nonexistent/file.md",
            artifact_path="/tmp/artifacts/output.md",
            run_root="/tmp",
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
