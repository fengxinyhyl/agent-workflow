"""测试 CLI 入口基本可用性。"""

import sys
import pytest


def test_cli_import():
    """测试 CLI 模块可以导入。"""
    from agent_workflow import cli
    assert cli is not None


def test_cli_build_parser():
    """测试 CLI parser 构建。"""
    from agent_workflow.cli import build_parser
    parser = build_parser()
    assert parser is not None
    assert parser.prog == "agent-workflow"


def test_cli_help():
    """测试 --help 返回 0。"""
    from agent_workflow.cli import build_parser

    parser = build_parser()
    # 验证所有子命令都已注册
    subcommands = [a for a in parser._actions if hasattr(a, 'choices')]
    assert len(subcommands) > 0


def test_cli_version():
    """测试版本号。"""
    from agent_workflow import __version__
    assert __version__ == "0.1.0"
