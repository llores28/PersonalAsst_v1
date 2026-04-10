"""Tool manifest schema — Pydantic model for tool-manifest-v1.

Resolves PRD gap A2 (tool manifest format).
"""

from typing import Optional

from pydantic import BaseModel, Field


class ToolCredential(BaseModel):
    """Schema for a declared credential a tool requires."""

    description: str = Field(description="What this credential is for")
    required: bool = Field(default=True)
    env_var_hint: str = Field(
        default="",
        description="Hint for the env var name the user should set (e.g. LINKEDIN_EMAIL)",
    )


class ToolParameter(BaseModel):
    """Schema for a single tool parameter."""

    type: str = Field(description="Python type hint: str, int, float, bool, list[str], etc.")
    required: bool = Field(default=True)
    description: str = Field(default="")
    default: Optional[str] = Field(default=None)


class ToolManifest(BaseModel):
    """Schema for tools/*/manifest.json — validates tool registration."""

    schema_: str = Field(alias="$schema", default="tool-manifest-v1")
    name: str = Field(description="Unique tool name (snake_case)")
    version: str = Field(default="1.0.0")
    description: str = Field(description="Human-readable description of what the tool does")
    type: str = Field(description="cli | function | mcp")
    entrypoint: str = Field(description="Main script filename (e.g. cli.py)")
    wrapper: str = Field(default="tool.py", description="function_tool wrapper filename")
    parameters: dict[str, ToolParameter] = Field(default_factory=dict)
    output_format: str = Field(default="text", description="json | text")
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    requires_approval: bool = Field(default=False)
    requires_network: bool = Field(default=False)
    allowed_hosts: list[str] = Field(default_factory=list)
    credentials: dict[str, ToolCredential] = Field(
        default_factory=dict,
        description="Declared credentials the tool needs (key = credential name)",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Python packages required (e.g. ['linkedin-api>=2.0.0'])",
    )
    created_at: Optional[str] = Field(default=None)
    created_by: str = Field(default="manual", description="manual | tool_factory")

    model_config = {"populate_by_name": True}

    def get_cli_args_template(self) -> list[str]:
        """Generate CLI argument template from parameters."""
        args = []
        for name, param in self.parameters.items():
            args.append(f"--{name}")
            args.append(f"{{{name}}}")
        return args
