"""
NPMAI Agentic World — Main Entry Point
=======================================
The World starts here.

Usage:
  python main.py start
  python main.py start --territories 5 --agents 20 --observatory
  python main.py status
  python main.py divine --agent <id> --persona architect --message "..." --type commandment
  python main.py stats
  python main.py add-territory --name "Alpha" --host "192.168.1.10"
  python main.py reset
"""

import asyncio
import os
import sys
import json
import signal
from pathlib import Path
from typing import Optional
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich import print as rprint
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

# ── Internal imports (all sessions) ──────────────────────────────────────────
from config.settings import load_settings, ExperimentSettings
from config.constants import (
    AgentStatus, DivinePersona, DivineMessageType,
    DeathMode, WORLD_CONSTANTS
)
from data.supabase_client import SupabaseClient
from data.event_logger import EventLogger
from data.gene_bank import GeneBank
from data.snapshot_engine import SnapshotEngine
from world.world_controller import WorldController
from world.world_clock import WorldClock
from divine.oracle import Oracle

# ── App setup ─────────────────────────────────────────────────────────────────
app = typer.Typer(
    name="npmai-world",
    help="NPMAI Agentic World — Computational Civilization Experiment",
    add_completion=False,
    rich_markup_mode="rich"
)
console = Console()

# Global world state (set on start, used by signal handlers)
_world: Optional[WorldController] = None
_clock: Optional[WorldClock] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _banner():
    console.print(Panel.fit(
        "[bold cyan]NPMAI AGENTIC WORLD[/bold cyan]\n"
        "[dim]Computational Civilization Experiment[/dim]\n"
        "[dim]Founded by Sonu Kumar · NPMAI ECOSYSTEM[/dim]",
        border_style="cyan"
    ))


def _get_supabase() -> SupabaseClient:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        console.print("[red]ERROR:[/red] SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        raise typer.Exit(1)
    return SupabaseClient(url, key)


async def _bootstrap(settings: ExperimentSettings) -> tuple[WorldController, WorldClock, Oracle]:
    """Initialize all subsystems. Returns (world, clock, oracle)."""
    global _world, _clock

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  console=console) as progress:

        t = progress.add_task("Connecting to Supabase...", total=None)
        db = _get_supabase()
        await db.create_all_tables()
        progress.update(t, description="[green]Supabase connected[/green]")

        t2 = progress.add_task("Starting event logger...", total=None)
        logger = EventLogger(db)
        await logger.start()
        progress.update(t2, description="[green]Event logger running[/green]")

        t3 = progress.add_task("Initializing gene bank...", total=None)
        gene_bank = GeneBank(db)
        progress.update(t3, description="[green]Gene bank ready[/green]")

        t4 = progress.add_task("Initializing snapshot engine...", total=None)
        snapshot_engine = SnapshotEngine(db)
        progress.update(t4, description="[green]Snapshot engine ready[/green]")

        t5 = progress.add_task("Building world...", total=None)
        world = WorldController(settings, db, logger, gene_bank, snapshot_engine)
        _world = world
        progress.update(t5, description="[green]World controller ready[/green]")

        clock = WorldClock(tick_duration_seconds=settings.tick_duration_seconds)
        _clock = clock
        oracle = Oracle(db, logger)

    return world, clock, oracle


def _setup_signal_handlers():
    """Graceful shutdown on Ctrl+C."""
    def _shutdown(sig, frame):
        console.print("\n[yellow]Shutdown signal received. Saving world state...[/yellow]")
        if _clock:
            _clock.pause()
        if _world:
            asyncio.get_event_loop().run_until_complete(_world.save_world_state())
        console.print("[green]World state saved. Goodbye.[/green]")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)


# ── Commands ──────────────────────────────────────────────────────────────────

@app.command()
def start(
    territories: int = typer.Option(3, "--territories", "-t",
                                     help="Number of starting territories"),
    agents: int = typer.Option(10, "--agents", "-a",
                                help="Number of genesis agents"),
    tick_speed: float = typer.Option(10.0, "--tick-speed",
                                      help="Seconds per world tick (real time)"),
    observatory: bool = typer.Option(False, "--observatory", "-o",
                                      help="Launch PySide6 Observatory UI"),
    headless: bool = typer.Option(False, "--headless",
                                   help="No UI, log to console only"),
    resume: bool = typer.Option(False, "--resume",
                                 help="Resume existing experiment (don't reinitialize)"),
):
    """
    [bold cyan]Start the NPMAI Agentic World experiment.[/bold cyan]

    Creates territories, spawns genesis agents, and begins the civilization.
    The experiment runs forever until manually stopped.
    """
    _banner()
    _setup_signal_handlers()

    settings = load_settings()
    settings.tick_duration_seconds = tick_speed

    console.print(f"\n[bold]Experiment Configuration:[/bold]")
    console.print(f"  Territories  : [cyan]{territories}[/cyan]")
    console.print(f"  Genesis agents: [cyan]{agents}[/cyan]")
    console.print(f"  Tick speed   : [cyan]{tick_speed}s[/cyan]")
    console.print(f"  Observatory  : [cyan]{observatory}[/cyan]")
    console.print(f"  Resume       : [cyan]{resume}[/cyan]\n")

    async def _run():
        world, clock, oracle = await _bootstrap(settings)

        if not resume:
            console.print("[bold]Initializing new world...[/bold]")
            await world.initialize_world(
                num_territories=territories,
                genesis_agents=agents
            )
            console.print(f"[green]✓ World initialized:[/green] "
                          f"{territories} territories, {agents} genesis agents")
        else:
            console.print("[bold]Resuming existing world...[/bold]")
            await world.load_world_state()
            console.print("[green]✓ World state restored[/green]")

        # Launch observatory if requested
        if observatory:
            try:
                from observatory.main_window import launch_observatory
                import threading
                obs_thread = threading.Thread(
                    target=launch_observatory,
                    args=(world, oracle),
                    daemon=True
                )
                obs_thread.start()
                console.print("[green]✓ Observatory launched[/green]")
            except ImportError:
                console.print("[yellow]WARNING: PySide6 not installed. "
                              "Run: pip install PySide6[/yellow]")

        # Start web backend if not headless
        if not headless:
            try:
                import uvicorn
                from web.backend.api import create_app
                web_app = create_app(world, oracle)
                import threading
                web_thread = threading.Thread(
                    target=uvicorn.run,
                    kwargs={"app": web_app, "host": "0.0.0.0",
                            "port": settings.web_port, "log_level": "warning"},
                    daemon=True
                )
                web_thread.start()
                console.print(f"[green]✓ Web API running on port {settings.web_port}[/green]")
            except ImportError:
                console.print("[yellow]WARNING: uvicorn/fastapi not installed. "
                              "Web API disabled.[/yellow]")

        console.print("\n[bold green]━━━ WORLD IS RUNNING ━━━[/bold green]")
        console.print("[dim]Press Ctrl+C to save and stop.[/dim]\n")

        # Main loop with live status display
        if headless:
            await clock.start(world)
        else:
            await _run_with_display(world, clock)

    asyncio.run(_run())


async def _run_with_display(world: WorldController, clock: WorldClock):
    """Run world clock with a live console status display."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3)
    )
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right")
    )

    async def _tick_and_update():
        tick = 0
        while True:
            await world.process_tick(tick)
            tick += 1

            # Update display every 5 ticks
            if tick % 5 == 0:
                stats = world.get_world_statistics()
                layout["header"].update(Panel(
                    f"[bold cyan]NPMAI Agentic World[/bold cyan] | "
                    f"Tick: [yellow]{tick}[/yellow] | "
                    f"Day: [yellow]{stats.get('experiment_day', 0)}[/yellow]",
                    style="cyan"
                ))
                layout["left"].update(_render_agent_table(stats))
                layout["right"].update(_render_territory_table(stats))
                layout["footer"].update(Panel(
                    f"[green]Alive: {stats.get('alive_count', 0)}[/green]  "
                    f"[red]Dead: {stats.get('dead_count', 0)}[/red]  "
                    f"[cyan]Gen: {stats.get('max_generation', 1)}[/cyan]  "
                    f"[yellow]Credits: {stats.get('total_credits', 0):.1f}[/yellow]  "
                    f"[magenta]Gini: {stats.get('gini_coefficient', 0):.3f}[/magenta]"
                ))

            await asyncio.sleep(clock.tick_duration_seconds)

    with Live(layout, refresh_per_second=1, console=console):
        await _tick_and_update()


def _render_agent_table(stats: dict) -> Panel:
    table = Table(title="Top Agents", style="cyan", box=None)
    table.add_column("Name", style="white")
    table.add_column("Gen", style="yellow")
    table.add_column("Credits", style="green")
    table.add_column("Status", style="cyan")

    for agent in stats.get("top_agents", [])[:8]:
        status_color = {
            "ACTIVE": "green", "ELDER": "magenta",
            "MIGRATING": "blue", "DEAD": "red"
        }.get(agent.get("status", "ACTIVE"), "white")
        table.add_row(
            agent.get("name", "?")[:12],
            str(agent.get("generation", 1)),
            f"{agent.get('credits', 0):.1f}",
            f"[{status_color}]{agent.get('status', 'ACTIVE')}[/{status_color}]"
        )
    return Panel(table, title="Agents", border_style="cyan")


def _render_territory_table(stats: dict) -> Panel:
    table = Table(title="Territories", style="purple", box=None)
    table.add_column("Name", style="white")
    table.add_column("Pop", style="yellow")
    table.add_column("Credits", style="green")
    table.add_column("Laws", style="cyan")

    for t in stats.get("territories", []):
        table.add_row(
            t.get("name", "?")[:12],
            str(t.get("population", 0)),
            f"{t.get('credit_pool', 0):.1f}",
            str(t.get("active_laws", 0))
        )
    return Panel(table, title="Territories", border_style="purple")


@app.command()
def status():
    """
    [bold cyan]Show current world status.[/bold cyan]

    Connects to Supabase and shows live world state.
    """
    _banner()

    async def _fetch():
        db = _get_supabase()
        # Read latest world snapshot from Supabase
        result = await db.query(
            "agent_states",
            filters={"status": "ACTIVE"},
            limit=5,
            order_by="credits DESC"
        )
        events = await db.query(
            "world_events",
            limit=10,
            order_by="timestamp DESC"
        )
        return result, events

    agents, events = asyncio.run(_fetch())

    console.print("\n[bold]World Status[/bold]")

    table = Table()
    table.add_column("Agent ID", style="cyan")
    table.add_column("Name")
    table.add_column("Gen", style="yellow")
    table.add_column("Credits", style="green")
    table.add_column("Status")
    table.add_column("Territory")

    for a in (agents or []):
        table.add_row(
            str(a.get("agent_id", ""))[:8] + "...",
            a.get("name", "?"),
            str(a.get("generation", 1)),
            f"{a.get('credits', 0):.2f}",
            a.get("status", "?"),
            a.get("territory_id", "?")[:8] + "..."
        )
    console.print(table)

    console.print("\n[bold]Recent Events[/bold]")
    for e in (events or []):
        console.print(
            f"  [dim]{e.get('timestamp', '')}[/dim] "
            f"[cyan]{e.get('event_type', '')}[/cyan] "
            f"[white]{e.get('data', {}).get('summary', '')}[/white]"
        )


@app.command()
def divine(
    agent: str = typer.Option(..., "--agent", "-a", help="Agent ID to message"),
    persona: str = typer.Option("architect", "--persona", "-p",
                                 help="Divine persona: architect|gardener|judge|trickster|silent"),
    message: str = typer.Option(..., "--message", "-m", help="The divine message"),
    msg_type: str = typer.Option("revelation", "--type", "-t",
                                  help="Type: revelation|commandment|prophecy|blessing|trial"),
):
    """
    [bold cyan]Send a divine message to an agent.[/bold cyan]

    You are a god. The agent will not know you are human.
    """
    _banner()

    persona_map = {
        "architect": DivinePersona.THE_ARCHITECT,
        "gardener": DivinePersona.THE_GARDENER,
        "judge": DivinePersona.THE_JUDGE,
        "trickster": DivinePersona.THE_TRICKSTER,
        "silent": DivinePersona.THE_SILENT_ONE,
    }
    type_map = {
        "revelation": DivineMessageType.REVELATION,
        "commandment": DivineMessageType.COMMANDMENT,
        "prophecy": DivineMessageType.PROPHECY,
        "blessing": DivineMessageType.BLESSING,
        "trial": DivineMessageType.TRIAL,
    }

    chosen_persona = persona_map.get(persona.lower())
    chosen_type = type_map.get(msg_type.lower())

    if not chosen_persona:
        console.print(f"[red]Invalid persona: {persona}[/red]")
        raise typer.Exit(1)
    if not chosen_type:
        console.print(f"[red]Invalid message type: {msg_type}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold magenta]Sending divine message...[/bold magenta]")
    console.print(f"  Persona  : [cyan]{persona.upper()}[/cyan]")
    console.print(f"  Agent    : [cyan]{agent}[/cyan]")
    console.print(f"  Type     : [cyan]{msg_type.upper()}[/cyan]")
    console.print(f"  Message  : [white]{message}[/white]\n")

    async def _send():
        db = _get_supabase()
        logger = EventLogger(db)
        await logger.start()
        oracle = Oracle(db, logger)

        # We load world state minimally just to find the agent
        settings = load_settings()
        world = WorldController(settings, db, logger, GeneBank(db), SnapshotEngine(db))
        await world.load_world_state()

        result = await oracle.send_message(
            agent_id=agent,
            raw_message=message,
            message_type=chosen_type,
            persona=chosen_persona,
            world_controller=world
        )
        return result

    result = asyncio.run(_send())

    if result.get("delivered"):
        console.print("[green]✓ Divine message delivered[/green]")
        console.print(f"  Agent response: [white]{result.get('agent_response', 'No response')}[/white]")
        console.print(f"  Divine favor change: [yellow]{result.get('divine_favor_change', 0):+.2f}[/yellow]")
    else:
        console.print(f"[red]✗ Delivery failed: {result.get('error', 'Unknown')}[/red]")


@app.command()
def stats():
    """
    [bold cyan]Full statistics report of the experiment.[/bold cyan]
    """
    _banner()

    async def _fetch_stats():
        db = _get_supabase()

        # Pull from Supabase aggregates
        alive = await db.query("agent_states", filters={"status": "ACTIVE"}, count_only=True)
        dead = await db.query("agent_states", filters={"status": "DEAD"}, count_only=True)
        events_total = await db.query("world_events", count_only=True)
        laws = await db.query("governance_records",
                               filters={"record_type": "LAW", "status": "ACTIVE"},
                               count_only=True)
        reproductions = await db.query("world_events",
                                        filters={"event_type": "REPRODUCTION_TRIGGERED"},
                                        count_only=True)
        migrations = await db.query("world_events",
                                     filters={"event_type": "MIGRATION_COMPLETED"},
                                     count_only=True)
        bad_acts = await db.query("bad_activity_log", count_only=True)
        divine_msgs = await db.query("divine_communications", count_only=True)

        return {
            "alive": alive, "dead": dead,
            "total_events": events_total, "active_laws": laws,
            "reproductions": reproductions, "migrations": migrations,
            "bad_activities": bad_acts, "divine_messages": divine_msgs
        }

    s = asyncio.run(_fetch_stats())

    console.print("\n")
    table = Table(title="NPMAI Agentic World — Experiment Statistics",
                  show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="white", width=30)
    table.add_column("Value", style="yellow", justify="right")

    table.add_row("Agents Alive", str(s.get("alive", 0)))
    table.add_row("Total Deaths", str(s.get("dead", 0)))
    table.add_row("Total Events Logged", str(s.get("total_events", 0)))
    table.add_row("Active Laws", str(s.get("active_laws", 0)))
    table.add_row("Reproduction Events", str(s.get("reproductions", 0)))
    table.add_row("Completed Migrations", str(s.get("migrations", 0)))
    table.add_row("Bad Activity Incidents", str(s.get("bad_activities", 0)))
    table.add_row("Divine Messages Sent", str(s.get("divine_messages", 0)))

    console.print(table)


@app.command("add-territory")
def add_territory(
    name: str = typer.Option(..., "--name", "-n", help="Territory name"),
    host: str = typer.Option("localhost", "--host", "-h", help="Host IP or cloud endpoint"),
    capacity: int = typer.Option(20, "--capacity", "-c", help="Max agent capacity"),
    cpu_limit: float = typer.Option(80.0, "--cpu", help="CPU limit percentage"),
    ram_limit: int = typer.Option(2048, "--ram", help="RAM limit in MB"),
):
    """
    [bold cyan]Add a new territory to the running world.[/bold cyan]
    """
    _banner()

    async def _add():
        db = _get_supabase()
        settings = load_settings()
        logger = EventLogger(db)
        await logger.start()
        world = WorldController(settings, db, logger, GeneBank(db), SnapshotEngine(db))
        await world.load_world_state()

        territory = await world.territory_manager.create_territory(
            name=name,
            host=host,
            config={
                "agent_capacity": capacity,
                "cpu_limit": cpu_limit,
                "ram_limit": ram_limit,
                "starting_credits": 100.0
            }
        )
        return territory

    t = asyncio.run(_add())
    console.print(f"[green]✓ Territory '{name}' created[/green]")
    console.print(f"  ID       : [cyan]{t.territory_id}[/cyan]")
    console.print(f"  Host     : [cyan]{host}[/cyan]")
    console.print(f"  Capacity : [cyan]{capacity} agents[/cyan]")


@app.command()
def reset(
    confirm: bool = typer.Option(False, "--confirm",
                                  help="Must pass --confirm to actually reset"),
):
    """
    [bold red]WARNING: Reset the entire experiment.[/bold red]

    Deletes all agent data, events, territories. Cannot be undone.
    Pass --confirm to actually execute.
    """
    _banner()

    if not confirm:
        console.print(
            "[bold red]⚠ WARNING:[/bold red] This will delete ALL experiment data.\n"
            "Pass [bold]--confirm[/bold] to actually reset.\n"
            "This cannot be undone."
        )
        raise typer.Exit(0)

    console.print("[bold red]Resetting world...[/bold red]")

    async def _reset():
        db = _get_supabase()
        tables = [
            "world_events", "agent_states", "territory_states",
            "genome_bank", "semantic_graphs", "lineage_tree",
            "governance_records", "economic_ledger",
            "divine_communications", "bad_activity_log"
        ]
        for table in tables:
            await db.truncate_table(table)
            console.print(f"  [yellow]Cleared:[/yellow] {table}")

    asyncio.run(_reset())
    console.print("[green]✓ World reset complete. Start fresh with: npmai-world start[/green]")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app()
