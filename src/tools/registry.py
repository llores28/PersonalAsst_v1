"""Tool registry — discovers tools from src/tools/plugins/ directory, supports hot-reload.

Resolves PRD gap B2 (agent registration at runtime) via filesystem watching.
"""

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from agents import FunctionTool

from src.tools.manifest import ToolManifest, ToolParameter
from src.tools.sandbox import run_cli_tool

logger = logging.getLogger(__name__)


# Map Python-style type hints from the manifest to JSON Schema types.
# Unknown types fall back to "string" so tools still register.
_PY_TO_JSON_TYPE: dict[str, str] = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "list": "array",
    "list[str]": "array",
    "array": "array",
    "dict": "object",
    "object": "object",
}


def _manifest_params_to_strict_schema(
    parameters: dict[str, ToolParameter],
) -> dict[str, Any]:
    """Build an OpenAI-strict JSON schema from manifest parameters.

    OpenAI strict mode requires:
      - ``additionalProperties: false`` on every object.
      - Every declared property listed in ``required``.
    Optional manifest params are still included but typed as ``[t, "null"]``
    so the model can emit ``null`` when skipping them.
    """
    properties: dict[str, dict[str, Any]] = {}
    for name, param in parameters.items():
        raw_type = (param.type or "str").strip().lower()
        json_type = _PY_TO_JSON_TYPE.get(raw_type, "string")
        prop: dict[str, Any] = {}
        if param.required:
            prop["type"] = json_type
        else:
            prop["type"] = [json_type, "null"]
        if param.description:
            prop["description"] = param.description
        if raw_type in ("list", "array", "list[str]"):
            prop.setdefault("items", {"type": "string"})
        properties[name] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }


class ToolRegistry:
    """Discovers tools from src/tools/plugins/ directory, hot-reloads on changes."""

    def __init__(self, tools_dir: Path):
        self.tools_dir = tools_dir
        self._tools: dict[str, Callable] = {}
        self._manifests: dict[str, ToolManifest] = {}
        self._observer = None

    async def load_all(self) -> list[Callable]:
        """Scan plugins dir for manifest.json files, load all valid tools."""
        if not self.tools_dir.exists():
            logger.warning("Tools directory does not exist: %s", self.tools_dir)
            return []

        for manifest_path in self.tools_dir.glob("*/manifest.json"):
            if manifest_path.parent.name.startswith("_"):
                continue  # Skip _example and other underscore-prefixed dirs
            try:
                await self._load_tool(manifest_path)
            except Exception as e:
                logger.error("Failed to load tool from %s: %s", manifest_path, e)

        logger.info("Loaded %d tools from %s", len(self._tools), self.tools_dir)
        return list(self._tools.values())

    async def _load_tool(self, manifest_path: Path) -> None:
        """Validate manifest, create wrapper function, register tool."""
        raw = manifest_path.read_text()
        manifest = ToolManifest.model_validate_json(raw)

        tool_dir = manifest_path.parent

        if manifest.type == "cli":
            wrapper_func = self._create_cli_wrapper(tool_dir, manifest)
            self._tools[manifest.name] = wrapper_func
        elif manifest.type == "function":
            result = self._load_function_wrapper(tool_dir, manifest)
            if isinstance(result, list):
                # Multi-tool: register each tool individually
                for tool_fn in result:
                    fn_name = getattr(tool_fn, "name", None) or getattr(tool_fn, "__name__", manifest.name)
                    self._tools[fn_name] = tool_fn
            else:
                self._tools[manifest.name] = result
        else:
            logger.warning("Skipping tool %s: unsupported type '%s'", manifest.name, manifest.type)
            return

        self._manifests[manifest.name] = manifest
        logger.info("Tool registered: %s (type=%s)", manifest.name, manifest.type)

    def _create_cli_wrapper(self, tool_dir: Path, manifest: ToolManifest) -> FunctionTool:
        """Create a FunctionTool that invokes a CLI tool via subprocess.

        The JSON schema is built directly from the manifest's declared parameters
        so it satisfies OpenAI's strict-schema requirement (no ``additionalProperties``
        on ``**kwargs``-style signatures).
        """
        entrypoint = manifest.entrypoint
        timeout = manifest.timeout_seconds
        tool_name = manifest.name
        description = manifest.description
        credential_keys = list(manifest.credentials.keys()) if manifest.credentials else None

        params_schema = _manifest_params_to_strict_schema(manifest.parameters)

        async def _invoke_cli(_ctx: Any, args_json: str) -> str:
            try:
                args_dict = json.loads(args_json) if args_json else {}
            except json.JSONDecodeError:
                args_dict = {}

            cli_args: list[str] = []
            for key, value in args_dict.items():
                if value is None:
                    continue
                if isinstance(value, bool):
                    if value:
                        cli_args.append(f"--{key}")
                    continue
                if isinstance(value, (list, tuple)):
                    # Pass repeated --key value pairs for array params
                    for item in value:
                        cli_args.extend([f"--{key}", str(item)])
                    continue
                cli_args.extend([f"--{key}", str(value)])

            rc, stdout, stderr = await run_cli_tool(
                tool_dir, entrypoint, cli_args,
                timeout=timeout,
                credential_keys=credential_keys,
            )

            if rc != 0:
                return f"Tool error (exit {rc}): {stderr[:500]}"
            return stdout

        return FunctionTool(
            name=tool_name,
            description=description,
            params_json_schema=params_schema,
            on_invoke_tool=_invoke_cli,
            strict_json_schema=True,
        )

    def _load_function_wrapper(self, tool_dir: Path, manifest: ToolManifest) -> Callable:
        """Load a Python function_tool wrapper from the tool directory.

        The wrapper module must expose either:
        - ``tool_functions`` (list) — multiple function_tools to register
        - ``tool_function`` — a single function_tool
        """
        wrapper_path = tool_dir / manifest.wrapper
        if not wrapper_path.exists():
            raise FileNotFoundError(f"Wrapper not found: {wrapper_path}")

        spec = importlib.util.spec_from_file_location(
            f"tools.{manifest.name}.tool", str(wrapper_path)
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Multi-tool: module exposes a list of function_tools
        if hasattr(module, "tool_functions"):
            return module.tool_functions  # list[Callable]

        if hasattr(module, "tool_function"):
            return module.tool_function
        raise AttributeError(
            f"Wrapper {wrapper_path} missing 'tool_function' or 'tool_functions' attribute"
        )

    def get_tool(self, name: str) -> Optional[Callable]:
        """Get a registered tool by name."""
        return self._tools.get(name)

    def get_manifest(self, name: str) -> Optional[ToolManifest]:
        """Get a tool's manifest by name."""
        return self._manifests.get(name)

    def list_tools(self) -> list[dict]:
        """List all registered tools with their metadata."""
        return [
            {
                "name": name,
                "type": m.type,
                "description": m.description,
                "requires_approval": m.requires_approval,
            }
            for name, m in self._manifests.items()
        ]

    async def start_watching(self) -> None:
        """Watch plugins directory for new/changed tools (hot-reload).

        Uses watchdog to monitor filesystem changes. When a new manifest.json
        appears, the tool is loaded and registered automatically.
        """
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            registry = self

            class ToolChangeHandler(FileSystemEventHandler):
                def on_created(self, event):
                    if event.src_path.endswith("manifest.json"):
                        import asyncio
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(
                                registry._load_tool(Path(event.src_path))
                            )
                            logger.info("Hot-reload: new tool detected at %s", event.src_path)
                        except RuntimeError:
                            logger.warning("No running event loop for hot-reload")

                def on_modified(self, event):
                    if event.src_path.endswith("manifest.json"):
                        self.on_created(event)

            self._observer = Observer()
            self._observer.schedule(
                ToolChangeHandler(),
                str(self.tools_dir),
                recursive=True,
            )
            self._observer.daemon = True
            self._observer.start()
            logger.info("Tool registry watching %s for changes", self.tools_dir)

        except ImportError:
            logger.warning("watchdog not installed — tool hot-reload disabled")

    def stop_watching(self) -> None:
        """Stop the filesystem watcher."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None


# Singleton registry instance
_registry: Optional[ToolRegistry] = None


async def get_registry() -> ToolRegistry:
    """Get or create the singleton tool registry."""
    global _registry
    if _registry is None:
        _plugins_dir = Path(__file__).resolve().parent / "plugins"
        _registry = ToolRegistry(_plugins_dir)
        await _registry.load_all()
        await _registry.start_watching()
    return _registry
