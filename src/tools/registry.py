"""Tool registry — discovers tools from tools/ directory, supports hot-reload.

Resolves PRD gap B2 (agent registration at runtime) via filesystem watching.
"""

import importlib.util
import logging
from pathlib import Path
from typing import Callable, Optional

from agents import function_tool

from src.tools.manifest import ToolManifest
from src.tools.sandbox import run_cli_tool
from src.settings import settings

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Discovers tools from tools/ directory, hot-reloads on changes."""

    def __init__(self, tools_dir: Path):
        self.tools_dir = tools_dir
        self._tools: dict[str, Callable] = {}
        self._manifests: dict[str, ToolManifest] = {}
        self._observer = None

    async def load_all(self) -> list[Callable]:
        """Scan tools/ for manifest.json files, load all valid tools."""
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
        elif manifest.type == "function":
            wrapper_func = self._load_function_wrapper(tool_dir, manifest)
        else:
            logger.warning("Skipping tool %s: unsupported type '%s'", manifest.name, manifest.type)
            return

        self._tools[manifest.name] = wrapper_func
        self._manifests[manifest.name] = manifest
        logger.info("Tool registered: %s (type=%s)", manifest.name, manifest.type)

    def _create_cli_wrapper(self, tool_dir: Path, manifest: ToolManifest) -> Callable:
        """Create a function_tool wrapper that calls a CLI tool via subprocess."""
        entrypoint = manifest.entrypoint
        timeout = manifest.timeout_seconds
        tool_name = manifest.name
        description = manifest.description

        @function_tool(name_override=tool_name, description_override=description)
        async def cli_wrapper(**kwargs: str) -> str:
            args = []
            for key, value in kwargs.items():
                args.extend([f"--{key}", str(value)])

            rc, stdout, stderr = await run_cli_tool(
                tool_dir, entrypoint, args, timeout=timeout
            )

            if rc != 0:
                return f"Tool error (exit {rc}): {stderr[:500]}"
            return stdout

        return cli_wrapper

    def _load_function_wrapper(self, tool_dir: Path, manifest: ToolManifest) -> Callable:
        """Load a Python function_tool wrapper from the tool directory."""
        wrapper_path = tool_dir / manifest.wrapper
        if not wrapper_path.exists():
            raise FileNotFoundError(f"Wrapper not found: {wrapper_path}")

        spec = importlib.util.spec_from_file_location(
            f"tools.{manifest.name}.tool", str(wrapper_path)
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "tool_function"):
            return module.tool_function
        raise AttributeError(f"Wrapper {wrapper_path} missing 'tool_function' attribute")

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
        """Watch tools/ directory for new/changed tools (hot-reload).

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
        _registry = ToolRegistry(Path("tools"))
        await _registry.load_all()
        await _registry.start_watching()
    return _registry
