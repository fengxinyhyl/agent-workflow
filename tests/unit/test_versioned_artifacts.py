"""测试版本化产物流传递方案。

覆盖：
1. TaskModel.version_strategy 字段
2. RunContext.promote_artifact_versioned / get_latest_version / get_version
3. ArtifactResolver 版本引用语法 (:latest / :vN / :all)
4. RunContext 序列化/反序列化版本链
"""

import os
import tempfile
import pytest

from agent_workflow.context import RunContext
from agent_workflow.artifacts.resolver import ArtifactResolver
from agent_workflow.config.models import TaskModel


class TestTaskModelVersionStrategy:
    """TaskModel version_strategy 字段测试。"""

    def test_default_is_overwrite(self):
        task = TaskModel(name="plan", output="plan_doc")
        assert task.version_strategy == "overwrite"

    def test_explicit_increment(self):
        task = TaskModel(name="plan", output="plan_doc", version_strategy="increment")
        assert task.version_strategy == "increment"

    def test_to_dict_includes_version_strategy(self):
        task = TaskModel(name="plan", output="plan_doc", version_strategy="increment")
        d = task.to_dict()
        assert d["version_strategy"] == "increment"

    def test_to_dict_default_overwrite(self):
        task = TaskModel(name="plan", output="plan_doc")
        d = task.to_dict()
        assert d["version_strategy"] == "overwrite"


class TestRunContextVersionedArtifacts:
    """RunContext 版本化产物流测试。"""

    def _make_ctx(self):
        return RunContext.create(
            workflow_id="test",
            goal="测试目标",
            project_root="/tmp",
            run_id="run_v001",
            run_root="/tmp/runs/run_v001",
        )

    def test_promote_versioned_first(self):
        ctx = self._make_ctx()
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v1.md")

        assert ctx.artifacts["plan_doc"] == "artifacts/plan_doc-v1.md"
        assert ctx.artifact_versions["plan_doc"] == ["artifacts/plan_doc-v1.md"]

    def test_promote_versioned_multiple(self):
        ctx = self._make_ctx()
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v1.md")
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v2.md")
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v3.md")

        # artifacts 始终指向最新版
        assert ctx.artifacts["plan_doc"] == "artifacts/plan_doc-v3.md"
        # 版本链保留完整历史
        assert ctx.artifact_versions["plan_doc"] == [
            "artifacts/plan_doc-v1.md",
            "artifacts/plan_doc-v2.md",
            "artifacts/plan_doc-v3.md",
        ]

    def test_get_latest_version(self):
        ctx = self._make_ctx()
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v1.md")
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v2.md")

        assert ctx.get_latest_version("plan_doc") == "artifacts/plan_doc-v2.md"

    def test_get_latest_version_no_versions(self):
        ctx = self._make_ctx()
        # 用旧的 promote_artifact（不走版本链）
        ctx.promote_artifact("plan_doc", "artifacts/plan_doc.md")

        assert ctx.get_latest_version("plan_doc") == "artifacts/plan_doc.md"

    def test_get_latest_version_nonexistent(self):
        ctx = self._make_ctx()
        assert ctx.get_latest_version("nonexistent") is None

    def test_get_version_by_number(self):
        ctx = self._make_ctx()
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v1.md")
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v2.md")
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v3.md")

        assert ctx.get_version("plan_doc", 1) == "artifacts/plan_doc-v1.md"
        assert ctx.get_version("plan_doc", 2) == "artifacts/plan_doc-v2.md"
        assert ctx.get_version("plan_doc", 3) == "artifacts/plan_doc-v3.md"

    def test_get_version_out_of_range(self):
        ctx = self._make_ctx()
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v1.md")

        assert ctx.get_version("plan_doc", 0) is None
        assert ctx.get_version("plan_doc", 5) is None

    def test_get_version_nonexistent_artifact(self):
        ctx = self._make_ctx()
        assert ctx.get_version("nonexistent", 1) is None

    def test_promote_versioned_backward_compat(self):
        """旧的 promote_artifact 不影响 artifact_versions。"""
        ctx = self._make_ctx()
        ctx.promote_artifact("plan_doc", "artifacts/plan_doc.md")

        assert ctx.artifacts["plan_doc"] == "artifacts/plan_doc.md"
        assert ctx.artifact_versions.get("plan_doc") is None or ctx.artifact_versions["plan_doc"] == []

    def test_mixed_promote_styles(self):
        """混用新旧 promote 时 get_latest_version 向后兼容。"""
        ctx = self._make_ctx()
        # 旧 style
        ctx.promote_artifact("plan_doc", "artifacts/plan_doc.md")
        # 新 style
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v2.md")

        assert ctx.get_latest_version("plan_doc") == "artifacts/plan_doc-v2.md"
        assert ctx.get_version("plan_doc", 1) == "artifacts/plan_doc-v2.md"

    def test_serialization_roundtrip(self):
        """版本链在序列化/反序列化后完整保留。"""
        ctx = self._make_ctx()
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v1.md")
        ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v2.md")
        ctx.record_state_visit("plan")

        json_str = ctx.to_json()
        ctx2 = RunContext.from_json(json_str)

        assert ctx2.artifact_versions == ctx.artifact_versions
        assert ctx2.get_latest_version("plan_doc") == "artifacts/plan_doc-v2.md"
        assert ctx2.get_version("plan_doc", 1) == "artifacts/plan_doc-v1.md"
        assert ctx2.get_version("plan_doc", 2) == "artifacts/plan_doc-v2.md"

    def test_save_load_version_chain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_v001")
            ctx = RunContext.create(
                workflow_id="test", goal="测试", project_root=tmpdir,
                run_id="run_v001", run_root=run_root,
            )
            ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v1.md")
            ctx.promote_artifact_versioned("plan_doc", "artifacts/plan_doc-v2.md")
            ctx.save()

            ctx2 = RunContext.load(run_root)
            assert ctx2.artifact_versions == ctx.artifact_versions
            assert ctx2.get_version("plan_doc", 2) == "artifacts/plan_doc-v2.md"


class TestArtifactResolverVersionSyntax:
    """ArtifactResolver 版本引用语法测试。"""

    def _make_ctx_and_files(self, tmpdir):
        """创建带版本化文件的 RunContext 和实际文件。"""
        run_root = os.path.join(tmpdir, "runs", "run_v001")
        artifacts_dir = os.path.join(run_root, "artifacts")
        os.makedirs(artifacts_dir, exist_ok=True)

        ctx = RunContext.create(
            workflow_id="test", goal="测试", project_root=tmpdir,
            run_id="run_v001", run_root=run_root,
        )

        # 创建版本化文件
        for v in range(1, 4):
            path = os.path.join(artifacts_dir, f"plan_doc-v{v}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# Plan v{v}\n\n第 {v} 版计划内容。\n")
            ctx.promote_artifact_versioned("plan_doc", path)

        return ctx, run_root

    def test_resolve_plain_name_returns_latest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, _ = self._make_ctx_and_files(tmpdir)
            resolver = ArtifactResolver(ctx)

            path = resolver.resolve("plan_doc")
            assert path is not None
            assert "plan_doc-v3.md" in path

    def test_resolve_latest_explicit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, _ = self._make_ctx_and_files(tmpdir)
            resolver = ArtifactResolver(ctx)

            path = resolver.resolve("plan_doc:latest")
            assert path is not None
            assert "plan_doc-v3.md" in path

    def test_resolve_v2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, _ = self._make_ctx_and_files(tmpdir)
            resolver = ArtifactResolver(ctx)

            path = resolver.resolve("plan_doc:v2")
            assert path is not None
            assert "plan_doc-v2.md" in path

    def test_resolve_v1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, _ = self._make_ctx_and_files(tmpdir)
            resolver = ArtifactResolver(ctx)

            path = resolver.resolve("plan_doc:v1")
            assert path is not None
            assert "plan_doc-v1.md" in path

    def test_resolve_v_out_of_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, _ = self._make_ctx_and_files(tmpdir)
            resolver = ArtifactResolver(ctx)

            path = resolver.resolve("plan_doc:v99")
            # 版本不存在，fallback 到文件系统查找
            assert path is None

    def test_resolve_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, _ = self._make_ctx_and_files(tmpdir)
            resolver = ArtifactResolver(ctx)

            path = resolver.resolve("plan_doc:all")
            assert path is not None
            assert "versions_summary" in path
            assert os.path.exists(path)

            # 验证摘要内容
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "v1" in content
            assert "v2" in content
            assert "v3" in content
            assert "第 1 版计划内容" in content
            assert "第 2 版计划内容" in content
            assert "第 3 版计划内容" in content

    def test_resolve_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, _ = self._make_ctx_and_files(tmpdir)
            resolver = ArtifactResolver(ctx)

            path = resolver.resolve("nonexistent")
            assert path is None

    def test_resolve_all_single_version(self):
        """单个版本时 :all 也能正常工作。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_root = os.path.join(tmpdir, "runs", "run_v001")
            artifacts_dir = os.path.join(run_root, "artifacts")
            os.makedirs(artifacts_dir, exist_ok=True)

            ctx = RunContext.create(
                workflow_id="test", goal="测试", project_root=tmpdir,
                run_id="run_v001", run_root=run_root,
            )
            path = os.path.join(artifacts_dir, "plan_doc.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# 单版本计划\n")

            # 使用旧 promote（没有版本链）
            ctx.promote_artifact("plan_doc", path)

            resolver = ArtifactResolver(ctx)
            result = resolver.resolve("plan_doc:all")
            assert result is not None
            assert os.path.exists(result)
            with open(result, "r", encoding="utf-8") as f:
                content = f.read()
            assert "单版本计划" in content

    def test_build_input_context_includes_all_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, _ = self._make_ctx_and_files(tmpdir)
            resolver = ArtifactResolver(ctx)

            context = resolver.build_input_context([
                "plan_doc:latest",
                "plan_doc:v1",
            ])
            assert "第 3 版计划内容" in context
            assert "第 1 版计划内容" in context

    def test_backward_compat_no_syntax(self):
        """不带语法的引用保持向后兼容。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, _ = self._make_ctx_and_files(tmpdir)
            resolver = ArtifactResolver(ctx)

            path = resolver.resolve("plan_doc")
            assert path is not None
            assert "plan_doc-v3.md" in path
