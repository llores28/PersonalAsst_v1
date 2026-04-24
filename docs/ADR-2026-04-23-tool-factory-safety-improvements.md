# ADR: Tool Factory Safety and System-Binary Support Improvements

**Date:** 2026-04-23  
**Status:** Implemented  
**Authors:** Cascade (AI Assistant)  
**Related:** FR-022, FR-023, FR-024, Phase 8 Organization Project Setup

## Context

The Tool Factory was experiencing critical failures when generating CLI tools that interact with system binaries like FFmpeg. Three distinct issues were preventing successful tool creation and registration:

1. **Sandbox Path Bug**: Tools were being executed with duplicated paths, causing all sandbox tests to fail
2. **Overly Restrictive Static Analysis**: Safe subprocess patterns using variables were rejected
3. **Invalid Skill References**: The planning prompt was inventing non-existent skill IDs

These issues blocked the Organization Project Setup workflow, preventing users from creating functional projects with system-binary dependencies.

## Decision

We implemented a comprehensive fix addressing all three failure modes while maintaining security guarantees:

### 1. Sandbox Path Resolution Fix

**Problem**: The sandbox was constructing paths like `/app/src/tools/plugins/<tool>/src/tools/plugins/<tool>/cli.py`

**Solution**: Use `Path.resolve()` to ensure absolute, non-duplicated paths:
```python
script_path = (tool_dir / entrypoint).resolve()
tool_dir = tool_dir.resolve()
```

### 2. Enhanced Static Analysis for System-Binary Tools

**Problem**: Only inline list literals were accepted, rejecting safe patterns like:
```python
cmd = ["ffmpeg", "-i", input_file, output_file]
subprocess.run(cmd, ...)
```

**Solution**: AST-based analysis that tracks variable assignments:
- Parse code with `ast.parse()`
- Track assignments of list literals to variables
- Resolve variable references in subprocess calls
- Normalize binary names (strip paths, lowercase)

### 3. Planning Prompt Skill Validation

**Problem**: Planner could invent skill IDs like `scheduler_diagnostics` that don't exist

**Solution**: Restrict to real internal skill IDs only:
```python
# For skills, use ONLY these IDs (or leave the list empty): memory, scheduler, organizations
# Prefer leaving skills empty unless an internal Atlas skill is genuinely needed.
```

### 4. System-Binary Tool Template

Added explicit code template in planning instructions to ensure `--help` works without the binary:
```python
def main():
    parser = argparse.ArgumentParser(description="...")
    parser.add_argument("--input", required=True)
    args = parser.parse_args()  # --help exits here, before any binary call
    cmd = ["ffmpeg", "-i", args.input, ...]
    # ... subprocess call
```

## Implementation Details

### Files Modified

1. **`src/tools/sandbox.py`**
   - Fixed path resolution in `run_cli_tool()`
   - Rewrote `_uses_only_allowed_binaries()` with AST-based analysis
   - Enhanced `test_tool_in_sandbox()` to skip binary tests when binary missing

2. **`src/agents/org_agent.py`**
   - Updated planning prompt with real skill IDs
   - Added system-binary tool template
   - Restricted skill choices to existing internal skills

3. **`src/agents/tool_factory_agent.py`**
   - Added system-binary tool documentation
   - Included safe pattern examples

4. **`Dockerfile`**
   - Added `ffmpeg` and `imagemagick` to system dependencies

### Security Considerations

- **Allowlist Preserved**: Only whitelisted binaries (ffmpeg, ffprobe, convert, etc.) are permitted
- **No Shell Execution**: All subprocess calls must use list arguments (no shell=True)
- **Variable Tracking Safe**: Only simple assignments from list literals are tracked
- **Sandbox Isolation**: Tools still run in isolated subprocess environment

### Validation Improvements

- **Graceful Degradation**: Missing binaries don't delete tools, they skip sandbox tests
- **Real-time Feedback**: Validation errors stored in agent config for user visibility
- **Structured Logging**: All failures logged with specific error details

## Consequences

### Positive

1. **System-Binary Tools Work**: FFmpeg, ImageMagick, sox, yt-dlp tools can be created and registered
2. **Organization Setup Complete**: End-to-end project creation with agents, tasks, and tools
3. **Better Developer Experience**: Clear error messages and validation feedback
4. **Maintained Security**: All safety checks preserved, just more permissive for safe patterns

### Negative

1. **Increased Complexity**: AST-based analysis is more complex than regex
2. **Potential for False Positives**: Variable tracking could theoretically miss complex patterns (mitigated by keeping tracking simple)

### Neutral

1. **Docker Image Size**: Added ffmpeg/imagemagick increases image size (~200MB)
2. **No Breaking Changes**: All existing tools continue to work unchanged

## Future Considerations

1. **Dynamic Binary Detection**: Could automatically detect available binaries on the host
2. **More Complex Pattern Support**: Future extensions could support more complex variable patterns
3. **Binary Version Management**: Could enforce minimum binary versions for compatibility

## Verification

1. **Manual Testing**: Created and registered `ffmpeg_debug_probe` tool successfully
2. **Sandbox Tests**: Tools with missing binaries skip tests gracefully
3. **Organization Setup**: End-to-end FFmpeg Video Composer project creates all components
4. **Static Analysis**: Safe variable patterns now pass security checks

## Status

- ✅ Implemented and tested
- ✅ Documentation updated (PRD.md, DEVELOPER_GUIDE.md, AGENTS.md)
- ✅ Docker image rebuilt with system binaries
- ✅ Organization Project Setup workflow verified end-to-end
