"""Tests for Phase 5 tool system."""

import pytest
from pathlib import Path

from src.tools.manifest import ToolManifest, ToolParameter
from src.tools.sandbox import static_analysis, BLOCKED_IMPORTS_IN_GENERATED


class TestToolManifest:
    """Test tool manifest schema validation."""

    def test_parse_example_manifest(self) -> None:
        manifest_path = Path("src/tools/plugins/_example/manifest.json")
        if manifest_path.exists():
            m = ToolManifest.model_validate_json(manifest_path.read_text())
            assert m.name == "example_echo"
            assert m.type == "cli"
            assert "text" in m.parameters

    def test_minimal_manifest(self) -> None:
        m = ToolManifest(
            name="test_tool",
            description="A test tool",
            type="cli",
            entrypoint="cli.py",
        )
        assert m.name == "test_tool"
        assert m.timeout_seconds == 30
        assert m.requires_approval is False

    def test_manifest_with_parameters(self) -> None:
        m = ToolManifest(
            name="param_tool",
            description="Tool with params",
            type="cli",
            entrypoint="cli.py",
            parameters={
                "input_file": ToolParameter(
                    type="str",
                    required=True,
                    description="Path to input file",
                ),
                "verbose": ToolParameter(
                    type="bool",
                    required=False,
                    description="Enable verbose output",
                    default="false",
                ),
            },
        )
        assert len(m.parameters) == 2
        assert m.parameters["input_file"].required is True
        assert m.parameters["verbose"].default == "false"

    def test_get_cli_args_template(self) -> None:
        m = ToolManifest(
            name="args_tool",
            description="Test",
            type="cli",
            entrypoint="cli.py",
            parameters={
                "name": ToolParameter(type="str", description="Name"),
                "count": ToolParameter(type="int", description="Count"),
            },
        )
        args = m.get_cli_args_template()
        assert "--name" in args
        assert "--count" in args


class TestStaticAnalysis:
    """Test code static analysis for safety."""

    def test_safe_code_passes(self) -> None:
        code = '''
import argparse
import json
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    print(args.text)

if __name__ == "__main__":
    main()
'''
        violations = static_analysis(code)
        assert violations == []

    def test_subprocess_blocked(self) -> None:
        code = "import subprocess\nsubprocess.run(['ls'])"
        violations = static_analysis(code)
        assert len(violations) > 0
        assert any("subprocess" in v for v in violations)

    def test_eval_blocked(self) -> None:
        code = "result = eval(user_input)"
        violations = static_analysis(code)
        assert len(violations) > 0

    def test_os_environ_blocked(self) -> None:
        code = "import os\nkey = os.environ['SECRET']"
        violations = static_analysis(code)
        assert len(violations) > 0

    def test_shutil_blocked(self) -> None:
        code = "import shutil\nshutil.rmtree('/tmp')"
        violations = static_analysis(code)
        assert len(violations) > 0

    def test_pickle_blocked(self) -> None:
        code = "import pickle\ndata = pickle.loads(payload)"
        violations = static_analysis(code)
        assert len(violations) > 0

    @pytest.mark.parametrize("blocked", BLOCKED_IMPORTS_IN_GENERATED)
    def test_all_blocked_imports_detected(self, blocked: str) -> None:
        code = f"import something\n{blocked}\nprint('hello')"
        violations = static_analysis(code)
        assert len(violations) > 0, f"Expected {blocked} to be blocked"


class TestToolFactoryAgent:
    """Test Tool Factory agent creation."""

    def test_create_tool_factory_agent(self) -> None:
        from src.agents.tool_factory_agent import create_tool_factory_agent
        agent = create_tool_factory_agent()
        assert agent.name == "ToolFactoryAgent"
        assert len(agent.tools) == 3  # generate_cli_tool, list_available_tools, review_tool_code

    def test_instructions_contain_decision_tree(self) -> None:
        from src.agents.tool_factory_agent import TOOL_FACTORY_INSTRUCTIONS
        assert "standalone script" in TOOL_FACTORY_INSTRUCTIONS
        assert "CLI Tool (default)" in TOOL_FACTORY_INSTRUCTIONS
        assert "function_tool" in TOOL_FACTORY_INSTRUCTIONS
        assert "specialist agent" in TOOL_FACTORY_INSTRUCTIONS

    def test_instructions_contain_audit_first_template_requirements(self) -> None:
        from src.agents.tool_factory_agent import TOOL_FACTORY_INSTRUCTIONS
        assert "AUDIT_FIRST_SPECIALIST_TEMPLATE.md" in TOOL_FACTORY_INSTRUCTIONS
        lowered = TOOL_FACTORY_INSTRUCTIONS.lower()
        assert "scenario matrix" in lowered
        assert "runtime wireframe" in lowered
        assert "audit plan" in lowered

    def test_instructions_contain_safety_rules(self) -> None:
        from src.agents.tool_factory_agent import TOOL_FACTORY_INSTRUCTIONS
        assert "subprocess" in TOOL_FACTORY_INSTRUCTIONS
        assert "sandbox" in TOOL_FACTORY_INSTRUCTIONS.lower()
        assert "tests and audit checks" in TOOL_FACTORY_INSTRUCTIONS


class TestToolRegistry:
    """Test tool registry."""

    def test_registry_list_empty_on_no_tools(self) -> None:
        from src.tools.registry import ToolRegistry
        reg = ToolRegistry(Path("nonexistent_dir"))
        assert reg.list_tools() == []

    def test_registry_get_nonexistent_tool(self) -> None:
        from src.tools.registry import ToolRegistry
        reg = ToolRegistry(Path("src/tools/plugins"))
        assert reg.get_tool("nonexistent") is None
