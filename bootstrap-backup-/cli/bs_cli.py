"""
Bootstrap CLI Toolkit — main entry point.

Usage: python bootstrap/cli/bs_cli.py <subcommand> [options]

All tools emit structured JSON by default (--format json).
Use --format human for rich terminal output.
"""

import sys
import time
from pathlib import Path

import click

# Ensure the bootstrap package is importable
_CLI_DIR = Path(__file__).resolve().parent
_BOOTSTRAP_DIR = _CLI_DIR.parent
if str(_BOOTSTRAP_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_DIR.parent))

from bootstrap.cli.security import audit_log


class AuditGroup(click.Group):
    """Click group that audit-logs every subcommand invocation."""

    def invoke(self, ctx):
        start = time.time()
        exit_code = 0
        try:
            return super().invoke(ctx)
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 1
            raise
        except Exception:
            exit_code = 1
            raise
        finally:
            duration_ms = int((time.time() - start) * 1000)
            tool_name = ctx.invoked_subcommand or "bs-cli"
            params = dict(ctx.params) if ctx.params else {}
            audit_log(
                tool=tool_name,
                args=params,
                exit_code=exit_code,
                duration_ms=duration_ms,
            )


@click.group(cls=AuditGroup)
@click.version_option(version="0.1.0", prog_name="bs-cli")
def cli():
    """Bootstrap CLI Toolkit — sniper-agent tools for Cascade."""
    pass


# --- Lazy-load subcommands to minimize import overhead ---

@cli.command("prereqs")
@click.option("--format", "output_format", type=click.Choice(["json", "human", "yaml"]), default="json")
@click.option("--component", default=None, help="Check/guide a specific component only.")
@click.option("--guide", is_flag=True, help="Output setup instructions for missing components.")
@click.pass_context
def prereqs_cmd(ctx, output_format, component, guide):
    """Check prerequisites and guide setup for missing components."""
    from bootstrap.cli.tools.prereqs import run_prereqs
    run_prereqs(output_format=output_format, component=component, guide=guide)


@cli.command("smoketest")
@click.option("--format", "output_format", type=click.Choice(["json", "human", "yaml"]), default="json")
@click.option("--level", type=click.Choice(["quick", "full"]), default="quick", help="Test depth.")
@click.option("--project-dir", default=".", help="Project directory to test.")
@click.pass_context
def smoketest_cmd(ctx, output_format, level, project_dir):
    """Run tiered smoke tests on the project."""
    from bootstrap.cli.tools.smoketest import run_smoketest
    run_smoketest(output_format=output_format, level=level, project_dir=project_dir)


@cli.command("debug")
@click.argument("subcommand", type=click.Choice(["logs", "trace", "deps", "env", "ports", "secrets-scan"]))
@click.argument("args", nargs=-1)
@click.option("--format", "output_format", type=click.Choice(["json", "human", "yaml"]), default="json")
@click.option("--project-dir", default=".", help="Project directory.")
@click.pass_context
def debug_cmd(ctx, subcommand, args, output_format, project_dir):
    """Debug investigation tools."""
    from bootstrap.cli.tools.debug import run_debug
    run_debug(subcommand=subcommand, args=args, output_format=output_format, project_dir=project_dir)


@cli.command("research")
@click.argument("subcommand", type=click.Choice(["docs", "deps", "changelog", "compare"]))
@click.argument("args", nargs=-1)
@click.option("--format", "output_format", type=click.Choice(["json", "human", "yaml"]), default="json")
@click.pass_context
def research_cmd(ctx, subcommand, args, output_format):
    """Research and investigate dependencies, docs, and APIs."""
    from bootstrap.cli.tools.research import run_research
    run_research(subcommand=subcommand, args=args, output_format=output_format)


@cli.command("scrape")
@click.argument("subcommand", type=click.Choice(["page", "api", "links", "docs"]))
@click.argument("url")
@click.option("--format", "output_format", type=click.Choice(["json", "human", "yaml"]), default="json")
@click.option("--depth", default=2, help="Max crawl depth for docs subcommand.")
@click.pass_context
def scrape_cmd(ctx, subcommand, url, output_format, depth):
    """Webscraping tools for external docs and APIs."""
    from bootstrap.cli.tools.scrape import run_scrape
    run_scrape(subcommand=subcommand, url=url, output_format=output_format, depth=depth)


@cli.command("scaffold")
@click.argument("name")
@click.option("--description", default="", help="Tool description.")
@click.option("--format", "output_format", type=click.Choice(["json", "human", "yaml"]), default="json")
@click.pass_context
def scaffold_cmd(ctx, name, description, output_format):
    """Scaffold a new CLI tool from template."""
    from bootstrap.cli.tools.scaffold import run_scaffold
    run_scaffold(name=name, description=description, output_format=output_format)


@cli.command("local-env")
@click.argument("subcommand", type=click.Choice(["init", "build", "up", "down", "logs", "status", "validate"]))
@click.option("--format", "output_format", type=click.Choice(["json", "human", "yaml"]), default="json")
@click.option("--project-dir", default=".", help="Project directory.")
@click.pass_context
def local_env_cmd(ctx, subcommand, output_format, project_dir):
    """Local environment and container validation tools."""
    from bootstrap.cli.tools.local_env import run_local_env
    run_local_env(subcommand=subcommand, output_format=output_format, project_dir=project_dir)


if __name__ == "__main__":
    cli()
