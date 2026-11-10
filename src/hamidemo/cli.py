"""hamidemo: deploy HAMi demo environments, run demos, draw GPU allocation."""

from pathlib import Path

import typer

from . import demo, utilization

app = typer.Typer(no_args_is_help=True, help=__doc__)
env = typer.Typer(no_args_is_help=True, help="Demo environments")
app.add_typer(env, name="env")


@env.command("list")
def env_list() -> None:
    for name in demo.names("environments"):
        typer.echo(f"{name}: {demo.load('environments', name)[1]['title']}")


@env.command("connect")
def env_connect(name: str) -> None:
    """Fetch the kubeconfig over SSH and tunnel the API server to localhost:6443."""
    typer.echo(f"export KUBECONFIG={demo.connect(name)}")


@env.command("setup")
def env_setup(name: str, remove_conflicts: bool = typer.Option(False, help="Uninstall device plugins that clash with HAMi")) -> None:
    """Install HAMi and its node configuration with Helmfile."""
    demo.env_setup(name, remove_conflicts)


@app.command("list")
def list_demos() -> None:
    for name in demo.names("demos"):
        spec = demo.load("demos", name)[1]
        typer.echo(f"{name} [{spec['environment']}]: {spec['title']}")


@app.command()
def run(name: str, keep: bool = typer.Option(False, help="Leave the workloads running")) -> None:
    """Deploy a demo, check it, benchmark it, draw the allocation and tear it down."""
    typer.echo(f"Results in {demo.Run(name)(keep)}")


@app.command()
def utilization_chart(out: Path = Path("output/utilization.png"), title: str = "HAMi GPU allocation") -> None:
    """Draw the current per-GPU memory allocation from HAMi annotations."""
    gpus = utilization.collect()
    for g in gpus:
        typer.echo(f"{g.node} {g.uuid} {g.mode}: " + ", ".join(f"{s.pod} {s.kind} {s.mem}MiB" for s in g.slices))
    typer.echo(utilization.draw(gpus, out, title))
