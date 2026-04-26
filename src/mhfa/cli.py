"""``mhfa`` CLI entry — currently exposes one workflow: ``earnings-recap``."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from dotenv import load_dotenv


@click.group()
@click.option(
    "--env-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".env"),
    show_default=True,
    help="Load env vars from this file before running.",
)
def main(env_file: Path) -> None:
    """Multi-Horizon Financial Agent — tool-using research workflows."""
    if env_file.exists():
        load_dotenv(env_file)


@main.command("earnings-recap")
@click.argument("ticker")
@click.option("--output", "output_dir", type=click.Path(path_type=Path), default=Path("outputs"))
@click.option("--period", default="3mo", show_default=True, help="yfinance price period.")
@click.option("--skip-synthesis", is_flag=True, help="Run plan + tools but don't call Opus.")
@click.option("--print-brief/--no-print-brief", default=True, help="Echo the brief to stdout.")
def earnings_recap_cmd(
    ticker: str, output_dir: Path, period: str, skip_synthesis: bool, print_brief: bool
) -> None:
    """Run the earnings_recap workflow on TICKER."""
    from mhfa.workflows.earnings_recap import run_earnings_recap  # local import — fast --help

    result = run_earnings_recap(
        ticker, output_dir=output_dir, period=period, skip_synthesis=skip_synthesis
    )

    md_path = result["paths"]["brief"]
    raw_path = result["paths"]["raw"]
    chart_path = result["paths"]["chart"]
    errors = result["raw"]["_metadata"]["errors"]

    click.echo(f"\n  brief : {md_path or '(skipped)'}")
    click.echo(f"  chart : {chart_path or '(unavailable)'}")
    click.echo(f"  raw   : {raw_path}")
    click.echo(f"  tools : {len(result['plan'])} calls, {len(errors)} errors")
    for e in errors:
        click.echo(f"    ! {e['tool']}  {e['type']}: {e['error']}", err=True)

    if print_brief and result["brief_text"]:
        click.echo("\n" + "─" * 72 + "\n")
        click.echo(result["brief_text"])


@main.command("list-tickers")
def list_tickers_cmd() -> None:
    """Show which tickers have local SEC summaries available."""
    from mhfa.tools.sec import SECDataNotFound, list_indexed_tickers

    try:
        tickers = list_indexed_tickers()
    except SECDataNotFound as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)
    click.echo(f"{len(tickers)} tickers indexed locally:\n")
    click.echo(json.dumps(tickers, indent=2))


if __name__ == "__main__":
    main()
