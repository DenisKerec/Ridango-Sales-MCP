"""kb2 CLI — read-only client.

The kb2 database is populated and maintained centrally; this package is a thin
MCP client for querying it.

Commands:
  kb2 list             enumerate KB workspaces
  kb2 stats [--kb …]   counts of entities / capabilities / relations / chunks
  kb2 ask <query>      run the semantic layer end-to-end and print the answer
  kb2 mcp serve        run the MCP server over stdio (for Claude Desktop / Code)
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from kb2.db import get_session
from kb2.reader import list_kbs, stats

app = typer.Typer(no_args_is_help=True, help="Ridango knowledge graph v2 — read-only MCP client.")
console = Console()


@app.command("list")
def cmd_list():
    """List all KBs with their entity counts."""
    with get_session() as session:
        rows = list_kbs(session)
    if not rows:
        console.print("[yellow]no knowledge bases visible[/]. Check KB2_DATABASE_URL.")
        return
    table = Table(title="kb2 knowledge bases")
    table.add_column("slug")
    table.add_column("name")
    table.add_column("status")
    table.add_column("entities", justify="right")
    table.add_column("description", overflow="fold")
    for r in rows:
        table.add_row(
            r["slug"], r["name"], r["status"],
            str(r["entity_count"]), r.get("description") or "",
        )
    console.print(table)


@app.command("stats")
def cmd_stats(
    kb: str | None = typer.Option(None, "--kb", help="Limit to one KB"),
):
    """Show counts: entities by kind, capabilities by classification, relations, chunks."""
    with get_session() as session:
        s = stats(session, kb_slug=kb)
    if "error" in s:
        console.print(f"[red]{s['error']}[/]")
        raise typer.Exit(1)
    table = Table(title=f"kb2 stats — kb={kb or 'ALL'}")
    table.add_column("Group")
    table.add_column("Detail")
    table.add_column("Value", justify="right")
    for k, v in sorted(s["entities_by_kind"].items()):
        table.add_row("entity", k, str(v))
    for k, v in sorted(s["capabilities_by_classification"].items()):
        table.add_row("capability", k or "(unset)", str(v))
    table.add_row("relation", "—", str(s["relations"]))
    table.add_row("chunk", "—", str(s["chunks"]))
    console.print(table)


@app.command("ask")
def cmd_ask(
    query: str = typer.Argument(..., help="Natural-language question."),
    kb: str | None = typer.Option(None, "--kb", help="Optional KB scope for RAG fallback."),
    show_sql: bool = typer.Option(False, "--sql", help="Print the executed SQL."),
):
    """Run the semantic layer end-to-end and print the answer."""
    from kb2.semantic.service import answer

    with get_session() as session:
        result = answer(session, query, kb_slug=kb)
    console.rule(f"intent: {result['intent']}")
    console.print(f"[dim]{result['intent_explanation']}[/]")
    if show_sql and result.get("sql"):
        console.print()
        console.rule("sql")
        console.print(result["sql"], markup=False)
    console.rule("answer")
    console.print(result["answer_md"])


mcp_app = typer.Typer(no_args_is_help=True, help="MCP server commands.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def cmd_mcp_serve():
    """Run the kb2 MCP server over stdio (for Claude Desktop / Code config)."""
    from kb2.mcp import run_stdio_server

    run_stdio_server()


if __name__ == "__main__":
    app()
