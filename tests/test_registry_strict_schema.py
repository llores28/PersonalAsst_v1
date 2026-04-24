"""Regression tests for the OpenAI strict-schema fix in the tool registry.

Before the fix, ``_create_cli_wrapper`` used ``async def cli_wrapper(**kwargs: str)``
which caused the agents SDK to emit a schema with ``additionalProperties`` set,
which the OpenAI strict-schema enforcer rejects with:

    additionalProperties should not be set for object types.

After the fix, CLI tools are built via :class:`agents.FunctionTool` directly from
manifest parameters, producing a strict-compliant schema. These tests pin that
behaviour so it does not regress.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.manifest import ToolManifest, ToolParameter
from src.tools.registry import (
    ToolRegistry,
    _manifest_params_to_strict_schema,
)


class TestManifestParamsToStrictSchema:
    def test_required_string_param(self) -> None:
        schema = _manifest_params_to_strict_schema({
            "input": ToolParameter(type="str", required=True, description="File path"),
        })
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["input"]
        assert schema["properties"]["input"]["type"] == "string"
        assert schema["properties"]["input"]["description"] == "File path"

    def test_optional_param_uses_nullable_union(self) -> None:
        """Strict mode requires every property in ``required``; optional params
        express optionality via ``[type, 'null']`` unions instead."""
        schema = _manifest_params_to_strict_schema({
            "flag": ToolParameter(type="bool", required=False, description=""),
        })
        assert "flag" in schema["required"]
        assert schema["properties"]["flag"]["type"] == ["boolean", "null"]

    def test_unknown_type_falls_back_to_string(self) -> None:
        schema = _manifest_params_to_strict_schema({
            "x": ToolParameter(type="SomethingWeird", required=True, description=""),
        })
        assert schema["properties"]["x"]["type"] == "string"

    def test_list_param_has_items_clause(self) -> None:
        schema = _manifest_params_to_strict_schema({
            "items": ToolParameter(type="list[str]", required=True, description=""),
        })
        assert schema["properties"]["items"]["type"] == "array"
        assert schema["properties"]["items"]["items"] == {"type": "string"}

    def test_no_additional_properties_ever(self) -> None:
        """The whole reason this helper exists — never leak additionalProperties."""
        schema = _manifest_params_to_strict_schema({
            "a": ToolParameter(type="str", required=True, description=""),
            "b": ToolParameter(type="int", required=False, description=""),
        })
        assert schema["additionalProperties"] is False

    def test_empty_params_still_valid_strict_schema(self) -> None:
        schema = _manifest_params_to_strict_schema({})
        assert schema == {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }


class TestCliWrapperRegistersUnderStrictSchema:
    """End-to-end check: a representative FFmpeg manifest should produce a
    FunctionTool whose schema passes agents.strict_schema.ensure_strict_json_schema."""

    @pytest.fixture
    def ffmpeg_manifest(self) -> ToolManifest:
        return ToolManifest(
            name="ffmpeg_add_subtitles",
            description="Adds subtitles to a video file.",
            type="cli",
            entrypoint="cli.py",
            parameters={
                "video": ToolParameter(type="str", required=True, description="Input video path"),
                "subtitles": ToolParameter(type="str", required=True, description="Subtitles path"),
                "output": ToolParameter(type="str", required=True, description="Output path"),
            },
        )

    def test_registry_builds_strict_compliant_tool(self, tmp_path: Path, ffmpeg_manifest: ToolManifest) -> None:
        from agents import FunctionTool
        from agents.strict_schema import ensure_strict_json_schema

        tool_dir = tmp_path / "ffmpeg_add_subtitles"
        tool_dir.mkdir()

        registry = ToolRegistry(tmp_path)
        tool = registry._create_cli_wrapper(tool_dir, ffmpeg_manifest)

        assert isinstance(tool, FunctionTool)
        assert tool.name == "ffmpeg_add_subtitles"
        # This is the regression: before the fix, ensure_strict_json_schema raised.
        strict = ensure_strict_json_schema(dict(tool.params_json_schema))
        assert strict["additionalProperties"] is False
        assert set(strict["required"]) == {"video", "subtitles", "output"}

    @pytest.mark.asyncio
    async def test_invoke_builds_cli_args_from_json(
        self, tmp_path: Path, ffmpeg_manifest: ToolManifest, monkeypatch
    ) -> None:
        """The new invoker parses JSON args and turns them into ``--key value`` pairs."""
        tool_dir = tmp_path / "ffmpeg_add_subtitles"
        tool_dir.mkdir()

        captured: dict[str, list[str]] = {}

        async def _fake_run(tool_dir, entrypoint, args, *, timeout=None, credential_keys=None):
            captured["args"] = args
            return (0, "ok", "")

        monkeypatch.setattr("src.tools.registry.run_cli_tool", _fake_run)

        registry = ToolRegistry(tmp_path)
        tool = registry._create_cli_wrapper(tool_dir, ffmpeg_manifest)

        payload = json.dumps({"video": "a.mp4", "subtitles": "a.srt", "output": "o.mp4"})
        result = await tool.on_invoke_tool(None, payload)

        assert result == "ok"
        # Order follows manifest param insertion order
        assert captured["args"] == [
            "--video", "a.mp4",
            "--subtitles", "a.srt",
            "--output", "o.mp4",
        ]

    @pytest.mark.asyncio
    async def test_invoke_skips_null_optional_params(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        manifest = ToolManifest(
            name="optional_tool",
            description="Tool with an optional param",
            type="cli",
            entrypoint="cli.py",
            parameters={
                "required_arg": ToolParameter(type="str", required=True, description=""),
                "optional_arg": ToolParameter(type="str", required=False, description=""),
            },
        )
        tool_dir = tmp_path / "optional_tool"
        tool_dir.mkdir()

        captured: dict[str, list[str]] = {}

        async def _fake_run(tool_dir, entrypoint, args, *, timeout=None, credential_keys=None):
            captured["args"] = args
            return (0, "ok", "")

        monkeypatch.setattr("src.tools.registry.run_cli_tool", _fake_run)

        registry = ToolRegistry(tmp_path)
        tool = registry._create_cli_wrapper(tool_dir, manifest)

        # Model returns null for the optional param — the wrapper must not pass it.
        payload = json.dumps({"required_arg": "value", "optional_arg": None})
        await tool.on_invoke_tool(None, payload)
        assert "--optional_arg" not in captured["args"]
        assert captured["args"] == ["--required_arg", "value"]
