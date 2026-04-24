"""File-type aware verification for repair patches.

Picks the right validator based on the file extension and contents:

- ``.py``       → ``compile()`` syntax check (no third-party deps required)
- ``SKILL.md``  → load via :class:`src.skills.loader.SkillLoader`
- ``.md``       → UTF-8 sanity + non-empty
- ``.yaml/.yml``→ ``yaml.safe_load``
- ``.json``     → ``json.load``
- ``.toml``     → ``tomllib.load``

Why this exists: the previous repair pipeline defaulted to
``python -m ruff check <path>`` for every patched file, which (a) makes no
sense for non-Python files such as ``SKILL.md``, and (b) fails outright in
the runtime container because ``ruff`` is a dev-only dependency. This
module uses only stdlib + ``pyyaml`` (already in ``requirements.txt``), so
it works in every environment we ship.

Usage::

    python -m src.repair.verify_file path/to/file [path/to/other ...]

Exit code 0 on success, non-zero on failure. Designed to be allowlisted
in :mod:`src.repair.engine`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 fallback (project requires 3.12+)
    tomllib = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[2]
USER_SKILLS_DIR = REPO_ROOT / "src" / "user_skills"


def _verify_python(path: Path) -> tuple[bool, str]:
    source = path.read_text(encoding="utf-8")
    try:
        compile(source, str(path), "exec")
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc.msg} (line {exc.lineno})"
    return True, "syntax ok"


def _split_frontmatter(content: str) -> tuple[str, str]:
    """Mirror src.skills.loader.SkillLoader._split_frontmatter without the import chain."""
    lines = content.split("\n")
    if len(lines) >= 3 and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return "", content


def _verify_skill_md(path: Path) -> tuple[bool, str]:
    """Validate a SKILL.md by parsing the YAML frontmatter directly.

    Self-contained so it can run as a subprocess without requiring the full
    project settings environment (database_url etc.) to be configured.
    """
    import yaml  # pyyaml is in requirements.txt

    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    if not frontmatter.strip():
        return False, "missing YAML frontmatter (expected fenced --- block)"

    try:
        metadata = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        return False, f"YAML frontmatter parse error: {exc}"

    if not isinstance(metadata, dict):
        return False, "YAML frontmatter must be a mapping"

    missing = [field for field in ("name", "description") if not metadata.get(field)]
    if missing:
        return False, f"frontmatter missing required field(s): {', '.join(missing)}"

    if not body.strip():
        return False, "skill body is empty (no instructions after frontmatter)"

    skill_id = metadata.get("id") or path.parent.name
    return True, f"skill '{skill_id}' frontmatter and body ok"


def _verify_markdown(path: Path) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return False, "file is empty"
    return True, "markdown ok"


def _verify_yaml(path: Path) -> tuple[bool, str]:
    import yaml  # pyyaml is in requirements.txt

    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return False, f"YAML parse error: {exc}"
    return True, "yaml ok"


def _verify_json(path: Path) -> tuple[bool, str]:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"JSON parse error: {exc.msg} (line {exc.lineno} col {exc.colno})"
    return True, "json ok"


def _verify_toml(path: Path) -> tuple[bool, str]:
    if tomllib is None:
        return True, "toml verifier unavailable (Python < 3.11) — skipped"
    try:
        with path.open("rb") as fh:
            tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return False, f"TOML parse error: {exc}"
    return True, "toml ok"


def _verify_text(path: Path) -> tuple[bool, str]:
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return False, f"not valid UTF-8: {exc}"
    return True, "utf-8 ok"


def _pick_verifier(path: Path) -> tuple[str, Callable[[Path], tuple[bool, str]]]:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python", _verify_python
    if suffix == ".md":
        # Treat any SKILL.md inside src/user_skills/ as a skill
        try:
            path.resolve().relative_to(USER_SKILLS_DIR.resolve())
            inside_user_skills = True
        except ValueError:
            inside_user_skills = False
        if path.name == "SKILL.md" and inside_user_skills:
            return "skill-md", _verify_skill_md
        return "markdown", _verify_markdown
    if suffix in (".yaml", ".yml"):
        return "yaml", _verify_yaml
    if suffix == ".json":
        return "json", _verify_json
    if suffix == ".toml":
        return "toml", _verify_toml
    return "text", _verify_text


def verify_path(raw_path: str) -> tuple[bool, str, str]:
    """Verify a single file. Returns (ok, kind, message)."""
    path = Path(raw_path)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    if not path.exists():
        return False, "missing", f"file not found: {raw_path}"
    if not path.is_file():
        return False, "not-a-file", f"not a regular file: {raw_path}"
    kind, verifier = _pick_verifier(path)
    ok, message = verifier(path)
    return ok, kind, message


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m src.repair.verify_file <path> [<path> ...]", file=sys.stderr)
        return 2

    overall = 0
    for raw in argv:
        ok, kind, message = verify_path(raw)
        status = "ok" if ok else "FAIL"
        stream = sys.stdout if ok else sys.stderr
        print(f"[{status}] [{kind}] {raw} — {message}", file=stream)
        if not ok:
            overall = 1
    return overall


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
