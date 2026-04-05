#!/usr/bin/env python3
"""ICDEV™ Pulse CLI — Blog engine command-line interface.

Usage:
    python cli.py research              # Run web research for all topics
    python cli.py cluster               # Cluster research into article topics
    python cli.py draft [--topic TEXT]   # Draft a blog post
    python cli.py pipeline [--topic T]  # Run full pipeline (research → draft → export)
    python cli.py export <post_id>      # Export post as MDX + HTML
    python cli.py list                  # List all posts
    python cli.py review <post_id>      # Show post for review
    python cli.py approve <post_id>     # Approve post for publishing
    python cli.py reject <post_id>      # Reject post with notes
    python cli.py publish <post_id>     # Mark as published and export
    python cli.py schedule              # Start scheduled pipeline
    python cli.py init                  # Initialize database
    python cli.py authors add <name>    # Add an author
    python cli.py authors list          # List authors
"""

import json
import logging
import sys
from datetime import datetime, timezone
from uuid import uuid4

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown

# Add ICDEV™ root to path for imports
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

from tools.pulse.db import init_db, get_connection, get_row, insert_row, query_rows, update_row

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


@click.group()
def cli():
    """ICDEV™ Pulse — AI-powered blog engine for thought leadership."""
    pass


@cli.command()
def init():
    """Initialize the database."""
    init_db()
    console.print("[green]Database initialized at data/pulse.db[/green]")

    # Seed default author
    authors = query_rows("authors", limit=1)
    if not authors:
        insert_row("authors", {
            "id": f"author-{uuid4().hex[:8]}",
            "name": "ICDEV™ Team",
            "email": "team@icdev.ai",
            "bio": "The ICDEV™ team builds intelligent certified development tools.",
            "role": "admin",
        })
        console.print("[green]Default author 'ICDEV™ Team' created[/green]")


@cli.command()
@click.option("--max-results", default=20, help="Max results per topic")
def research(max_results):
    """Run web research for all configured topics."""
    init_db()
    from tools.pulse.engine.researcher import research_all_topics

    console.print("[bold]Researching trending topics...[/bold]")
    results = research_all_topics()
    console.print(f"[green]Found {len(results)} research results[/green]")

    table = Table(title="Research Results")
    table.add_column("Source", style="cyan")
    table.add_column("Title", style="white", max_width=50)
    table.add_column("Score", style="yellow")

    for r in results[:20]:
        table.add_row(
            r.get("source", "?"),
            (r.get("title", "")[:50] or r.get("snippet", "")[:50]),
            f"{r.get('relevance_score', 0):.2f}",
        )
    console.print(table)


@cli.command()
def cluster():
    """Cluster research results into article topics."""
    init_db()
    from tools.pulse.engine.topic_clusterer import cluster_research, rank_clusters

    results = query_rows("research_cache", limit=500)
    if not results:
        console.print("[yellow]No research data. Run 'research' first.[/yellow]")
        return

    clusters = cluster_research(results)
    clusters = rank_clusters(clusters)

    table = Table(title="Topic Clusters")
    table.add_column("Priority", style="yellow")
    table.add_column("Topic", style="cyan", max_width=40)
    table.add_column("Pain Points", style="white")
    table.add_column("Sources", style="green")

    for c in clusters:
        pain_points = c.get("pain_points", [])
        if isinstance(pain_points, str):
            pain_points = json.loads(pain_points)
        research_ids = c.get("research_ids", [])
        if isinstance(research_ids, str):
            research_ids = json.loads(research_ids)

        table.add_row(
            f"{c.get('priority_score', 0):.2f}",
            c.get("name", ""),
            str(len(pain_points)),
            str(len(research_ids)),
        )
    console.print(table)


@cli.command()
@click.option("--topic", default=None, help="Override topic (skip research)")
@click.option("--json-output", is_flag=True, help="Output as JSON")
def draft(topic, json_output):
    """Run research + clustering to prepare context for Claude Code to write."""
    init_db()
    from tools.pulse.engine.scheduler import research_phase

    console.print("[bold]Running research phase...[/bold]")
    result = research_phase(topic_override=topic)

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
    else:
        if result["status"] == "ok":
            cluster = result.get("cluster", {})
            synthesis = result.get("synthesis", {})
            console.print(Panel(
                f"[green bold]Research Complete[/green bold]\n\n"
                f"Cluster: {cluster.get('name', 'N/A')}\n"
                f"Priority: {cluster.get('priority_score', 0):.2f}\n"
                f"Research results: {len(result.get('research_results', []))}\n"
                f"Synthesis model: {synthesis.get('model_used', 'N/A')}\n\n"
                f"[cyan]Next step: Ask Claude Code to write the article[/cyan]",
                title="Ready for Drafting",
                border_style="green",
            ))
            if synthesis.get("synthesis"):
                console.print(Panel(
                    str(synthesis["synthesis"])[:500],
                    title="Research Synthesis (Ollama)",
                    border_style="blue",
                ))
        else:
            console.print(f"[red]Research failed: {result.get('error', 'Unknown')}[/red]")


@cli.command()
@click.option("--json-output", is_flag=True, help="Output as JSON")
def synthesize(json_output):
    """Synthesize existing research results using Ollama qwen3.5."""
    init_db()
    from tools.pulse.engine.researcher import synthesize_research

    results = query_rows("research_cache", limit=500)
    if not results:
        console.print("[yellow]No research data. Run 'research' first.[/yellow]")
        return

    console.print(f"[bold]Synthesizing {len(results)} research signals with Ollama...[/bold]")
    synthesis = synthesize_research(results)

    if json_output:
        click.echo(json.dumps(synthesis, indent=2, default=str))
    else:
        console.print(Panel(
            f"Model: {synthesis.get('model_used', 'N/A')}\n"
            f"Status: {synthesis.get('status', 'N/A')}\n\n"
            f"{str(synthesis.get('synthesis', ''))[:1000]}",
            title="Research Synthesis",
            border_style="cyan",
        ))
        if synthesis.get("recommended_angles"):
            console.print("\n[yellow]Recommended article angles:[/yellow]")
            for angle in synthesis["recommended_angles"]:
                if isinstance(angle, dict):
                    console.print(f"  - {angle.get('theme', angle)}")
                else:
                    console.print(f"  - {angle}")


@cli.command("pipeline")
@click.option("--topic", default=None, help="Override topic")
@click.option("--draft-file", default=None, type=click.Path(exists=True), help="Path to pre-written markdown draft")
def pipeline(topic, draft_file):
    """Process a pre-written draft through the pipeline (image, SEO, quality, export)."""
    init_db()

    if draft_file:
        if not topic:
            console.print("[red]--topic is required when using --draft-file[/red]")
            raise SystemExit(1)

        from pathlib import Path
        content = Path(draft_file).read_text(encoding="utf-8")
        from tools.pulse.engine.scheduler import run_pipeline_from_draft

        with console.status("[bold green]Running pipeline from draft..."):
            result = run_pipeline_from_draft(topic, content, [])
    else:
        console.print("[yellow]No --draft-file provided.[/yellow]")
        console.print("The pipeline now requires a pre-written draft from Claude Code.")
        console.print("\nUsage:")
        console.print("  python cli.py pipeline --topic 'Topic' --draft-file path/to/draft.md")
        console.print("\nOr use 'draft' command for research + clustering only:")
        console.print("  python cli.py draft --topic 'Topic'")
        return

    if result["status"] == "completed":
        console.print(f"[green]Pipeline complete![/green] Post: {result['post_id']}")
        console.print(f"  Title: {result['title']}")
        console.print(f"  Words: {result['word_count']}")
        console.print(f"  Quality: {result.get('quality_score', 0):.1f}")
        console.print(f"  WriteGuard: {'PASSED' if result.get('writeguard_passed') else 'NEEDS REVIEW'}")
        if result.get("rewrite_needed"):
            console.print(f"\n[yellow]Rewrite needed — {len(result.get('rewrite_findings', []))} findings[/yellow]")
            console.print("Ask Claude Code to rewrite based on quality findings.")
        console.print(f"\n  Review with: python cli.py review {result['post_id']}")
    else:
        console.print(f"[red]Failed: {result.get('error')}[/red]")


@cli.command("list")
@click.option("--status", default=None, help="Filter by status")
@click.option("--json-output", is_flag=True, help="Output as JSON")
def list_posts(status, json_output):
    """List all posts."""
    init_db()
    where = ""
    params = ()
    if status:
        where = "status = ?"
        params = (status,)

    posts = query_rows("posts", where=where, params=params, limit=100)

    if json_output:
        click.echo(json.dumps(posts, indent=2, default=str))
        return

    table = Table(title="Blog Posts")
    table.add_column("ID", style="dim")
    table.add_column("Status", style="cyan")
    table.add_column("Title", style="white", max_width=50)
    table.add_column("Words", style="green")
    table.add_column("Quality", style="yellow")
    table.add_column("Created", style="dim")

    for p in posts:
        status_style = {
            "draft": "yellow",
            "in_review": "cyan",
            "approved": "green",
            "published": "bold green",
            "rejected": "red",
        }.get(p.get("status", ""), "white")

        table.add_row(
            p["id"][:16],
            f"[{status_style}]{p.get('status', '?')}[/{status_style}]",
            p.get("title", "Untitled")[:50],
            str(p.get("word_count", 0)),
            f"{p.get('readability_score', 0):.0f}",
            (p.get("created_at", ""))[:10],
        )
    console.print(table)


@cli.command()
@click.argument("post_id")
def review(post_id):
    """Show a post for review."""
    init_db()
    post = get_row("posts", post_id)
    if not post:
        console.print(f"[red]Post not found: {post_id}[/red]")
        return

    console.print(Panel(
        f"[bold]{post.get('title', 'Untitled')}[/bold]\n"
        f"Status: {post.get('status')} | Words: {post.get('word_count', 0)} | "
        f"Quality: {post.get('readability_score', 0):.0f}\n"
        f"WriteGuard: {'PASSED' if post.get('writeguard_passed') else 'NEEDS REVIEW'}",
        title="Post Review",
        border_style="cyan",
    ))

    if post.get("tldr"):
        console.print(Panel(post["tldr"], title="TL;DR", border_style="blue"))

    body = post.get("body_markdown", "")
    if body:
        # Show first 2000 chars
        preview = body[:2000] + ("..." if len(body) > 2000 else "")
        console.print(Markdown(preview))
        if len(body) > 2000:
            console.print(f"\n[dim]... {len(body) - 2000} more characters. "
                          f"See full post in exports.[/dim]")

    console.print(f"\n[dim]Approve: python cli.py approve {post_id}[/dim]")
    console.print(f"[dim]Reject:  python cli.py reject {post_id} --notes 'reason'[/dim]")


@cli.command()
@click.argument("post_id")
@click.option("--reviewer", default="admin", help="Reviewer name")
def approve(post_id, reviewer):
    """Approve a post for publishing."""
    init_db()
    post = get_row("posts", post_id)
    if not post:
        console.print(f"[red]Post not found: {post_id}[/red]")
        return

    datetime.now(timezone.utc).isoformat()
    update_row("posts", post_id, {
        "status": "approved",
        "reviewer_id": reviewer,
    })
    insert_row("post_reviews", {
        "id": f"rev-{uuid4().hex[:12]}",
        "post_id": post_id,
        "reviewer_id": reviewer,
        "action": "approved",
    })
    console.print(f"[green]Post approved! Publish with: python cli.py publish {post_id}[/green]")


@cli.command()
@click.argument("post_id")
@click.option("--notes", default="", help="Rejection notes")
@click.option("--reviewer", default="admin", help="Reviewer name")
def reject(post_id, notes, reviewer):
    """Reject a post with notes."""
    init_db()
    post = get_row("posts", post_id)
    if not post:
        console.print(f"[red]Post not found: {post_id}[/red]")
        return

    update_row("posts", post_id, {
        "status": "rejected",
        "reviewer_id": reviewer,
        "review_notes": notes,
    })
    insert_row("post_reviews", {
        "id": f"rev-{uuid4().hex[:12]}",
        "post_id": post_id,
        "reviewer_id": reviewer,
        "action": "rejected",
        "notes": notes,
    })
    console.print(f"[yellow]Post rejected. Notes: {notes or '(none)'}[/yellow]")


@cli.command()
@click.argument("post_id")
def publish(post_id):
    """Mark post as published and re-export."""
    init_db()
    from tools.pulse.engine.exporter import export_both

    post = get_row("posts", post_id)
    if not post:
        console.print(f"[red]Post not found: {post_id}[/red]")
        return

    if post["status"] not in ("approved", "draft"):
        console.print(f"[yellow]Post status is '{post['status']}'. Approve first.[/yellow]")
        return

    now = datetime.now(timezone.utc).isoformat()
    update_row("posts", post_id, {
        "status": "published",
        "published_at": now,
    })

    exports = export_both(post_id)
    console.print("[green bold]Published![/green bold]")
    console.print(f"  MDX: {exports['mdx']}")
    console.print(f"  HTML: {exports['html']}")


@cli.command("export")
@click.argument("post_id")
@click.option("--format", "fmt", default="both", type=click.Choice(["mdx", "html", "both"]))
def export_cmd(post_id, fmt):
    """Export a post as MDX, HTML, or both."""
    init_db()
    from tools.pulse.engine.exporter import export_mdx, export_html, export_both

    post = get_row("posts", post_id)
    if not post:
        console.print(f"[red]Post not found: {post_id}[/red]")
        return

    if fmt == "mdx":
        path = export_mdx(post_id)
        console.print(f"[green]Exported: {path}[/green]")
    elif fmt == "html":
        path = export_html(post_id)
        console.print(f"[green]Exported: {path}[/green]")
    else:
        result = export_both(post_id)
        console.print(f"[green]Exported MDX: {result['mdx']}[/green]")
        console.print(f"[green]Exported HTML: {result['html']}[/green]")


@cli.command()
def schedule():
    """Start the scheduled pipeline (daily/weekly)."""
    init_db()
    from tools.pulse.engine.scheduler import run_scheduled

    console.print("[bold]Starting ICDEV™ Pulse scheduler...[/bold]")
    run_scheduled()


@cli.group()
def authors():
    """Manage authors."""
    pass


@authors.command("add")
@click.argument("name")
@click.option("--email", default="", help="Author email")
@click.option("--bio", default="", help="Author bio")
@click.option("--role", default="contributor", type=click.Choice(["admin", "editor", "contributor"]))
def add_author(name, email, bio, role):
    """Add a new author."""
    init_db()
    author_id = f"author-{uuid4().hex[:8]}"
    insert_row("authors", {
        "id": author_id,
        "name": name,
        "email": email,
        "bio": bio,
        "role": role,
    })
    console.print(f"[green]Author '{name}' added ({author_id})[/green]")


@authors.command("list")
def list_authors():
    """List all authors."""
    init_db()
    authors_list = query_rows("authors", limit=100)
    table = Table(title="Authors")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Role", style="yellow")
    table.add_column("Email", style="dim")

    for a in authors_list:
        table.add_row(a["id"], a["name"], a.get("role", ""), a.get("email", ""))
    console.print(table)


@cli.command("stats")
def stats():
    """Show pipeline statistics."""
    init_db()
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        drafts = conn.execute("SELECT COUNT(*) FROM posts WHERE status='draft'").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM posts WHERE status='approved'").fetchone()[0]
        published = conn.execute("SELECT COUNT(*) FROM posts WHERE status='published'").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM posts WHERE status='rejected'").fetchone()[0]
        research = conn.execute("SELECT COUNT(*) FROM research_cache").fetchone()[0]
        clusters = conn.execute("SELECT COUNT(*) FROM topic_clusters").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM schedule_log").fetchone()[0]

    console.print(Panel(
        f"Posts: {total} (drafts: {drafts}, approved: {approved}, "
        f"published: {published}, rejected: {rejected})\n"
        f"Research cache: {research} entries\n"
        f"Topic clusters: {clusters}\n"
        f"Pipeline runs: {runs}",
        title="ICDEV™ Pulse Stats",
        border_style="cyan",
    ))


# ---------------------------------------------------------------------------
# Hostinger publishing commands
# ---------------------------------------------------------------------------


@cli.group("hostinger")
def hostinger_group():
    """Hostinger Website Builder publishing commands."""
    pass


@hostinger_group.command("setup")
def hostinger_setup():
    """Open browser for manual Hostinger login (first-time setup)."""
    from tools.pulse.engine.hostinger_publisher import setup_session

    result = setup_session()
    if result.get("status") == "ok":
        console.print(Panel(
            f"Login completed!\n"
            f"Builder URL: {result.get('builder_url', 'N/A')}\n"
            f"Blog found: {result.get('blog_section', {}).get('found', False)}",
            title="Hostinger Setup Complete",
            border_style="green",
        ))
    else:
        console.print(f"[red]Setup failed: {result.get('message')}[/red]")


@hostinger_group.command("publish")
@click.argument("post_id")
def hostinger_publish(post_id):
    """Publish a specific post to Hostinger blog."""
    from tools.pulse.engine.hostinger_publisher import publish_post

    result = publish_post(post_id)
    if result.get("status") == "ok":
        console.print(Panel(
            f"Published: {result.get('title')}\n"
            f"Post ID: {result.get('post_id')}",
            title="Published to Hostinger",
            border_style="green",
        ))
    else:
        console.print(f"[red]Publish failed: {result.get('message')}[/red]")


@hostinger_group.command("publish-all")
def hostinger_publish_all():
    """Publish all approved posts to Hostinger blog."""
    from tools.pulse.engine.hostinger_publisher import publish_all_approved

    result = publish_all_approved()
    console.print(Panel(
        f"Total: {result.get('total', 0)} | "
        f"Succeeded: {result.get('succeeded', 0)} | "
        f"Failed: {result.get('failed', 0)}",
        title="Batch Publish Results",
        border_style="cyan",
    ))


@hostinger_group.command("session")
def hostinger_session():
    """Check Hostinger session health."""
    from tools.pulse.engine.hostinger_publisher import check_session

    result = check_session()
    color = {"ok": "green", "stale": "yellow", "no_session": "red"}.get(
        result.get("status"), "white"
    )
    console.print(f"[{color}]{result.get('message')}[/{color}]")


@hostinger_group.command("key-rotation")
def hostinger_key_rotation():
    """Check API key rotation status."""
    from tools.pulse.engine.hostinger_publisher import _check_key_rotation

    result = _check_key_rotation()
    color = {"ok": "green", "warning": "yellow", "expired": "red"}.get(
        result.get("status"), "white"
    )
    console.print(f"[{color}]{result.get('message', json.dumps(result))}[/{color}]")
    if result.get("rotation_due"):
        console.print(f"  Rotation due: {result['rotation_due']}")


if __name__ == "__main__":
    cli()
