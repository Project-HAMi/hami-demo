"""hamidemo: deploy HAMi demo environments, run demos, draw GPU allocation."""

import os
from pathlib import Path
from typing import Annotated

import typer

from . import demo, utilization
from . import env as envs
from . import logs as follow_logs

app = typer.Typer(no_args_is_help=True, help=__doc__)
env = typer.Typer(no_args_is_help=True, help="Demo environments")
app.add_typer(env, name="env")


@env.command("list")
def env_list() -> None:
    for name in envs.names():
        typer.echo(f"{name}: {envs.load(name)['title']}")


@env.command("connect")
def env_connect(name: str) -> None:
    """Pull the kubeconfig and start the SOCKS proxy in the background."""
    typer.echo(f"export KUBECONFIG={envs.connect(name)}")


@env.command("kubeconfig")
def env_kubeconfig(name: str, out: Annotated[Path | None, typer.Option(help="Write here instead of environments/<name>/kubeconfig")] = None) -> None:
    """Only pull the kubeconfig (context hamidemo-<name>); run `env proxy` next to it."""
    path = envs.kubeconfig(name, out)
    typer.echo(f"export KUBECONFIG={path}\n# needs the proxy: {' '.join(envs.proxy_args(name))}")


@env.command("proxy")
def env_proxy(name: str) -> None:
    """SOCKS proxy to the control plane in the foreground (Ctrl-C to stop). kubectl, helm and k9s use it via proxy-url."""
    args = envs.proxy_args(name)
    typer.echo(" ".join(args))
    os.execvp(args[0], args)


@env.command("setup")
def env_setup(name: str, remove_conflicts: bool = typer.Option(False, help="Uninstall device plugins that clash with HAMi")) -> None:
    """Create the kind cluster if the environment has one, then apply the Helmfile releases."""
    envs.setup(name, remove_conflicts)


@app.command("list")
def list_demos() -> None:
    for name in demo.names():
        spec = demo.load(name)[1]
        typer.echo(f"{name} [{spec['environment']}]: {spec['title']}")


KubeconfigOpt = Annotated[Path | None, typer.Option(help="Use this kubeconfig instead of the demo environment's (proxy auto-started)")]


def target(name: str, kubeconfig: Path | None) -> None:
    """Every demo names its environment; talk to that cluster, not whatever KUBECONFIG the shell has."""
    if kubeconfig:
        os.environ["KUBECONFIG"] = str(kubeconfig.expanduser())
    else:
        path = envs.use(demo.load(name)[1]["environment"])
        typer.secho(f"# {name}: KUBECONFIG={path}", fg="bright_black", err=True)


@app.command()
def run(name: str, keep: bool = typer.Option(False, help="Leave the workloads running"), kubeconfig: KubeconfigOpt = None) -> None:
    """Deploy a demo, check it, benchmark it, draw the allocation and tear it down."""
    target(name, kubeconfig)
    typer.echo(f"Results in {demo.Run(name)(keep)}")


@app.command()
def logs(name: str, tail: Annotated[int, typer.Option(help="Lines of history per container when it attaches")] = 20, kubeconfig: KubeconfigOpt = None) -> None:
    """Follow the logs of every workload the demo starts (run it in a second terminal next to `run`)."""
    target(name, kubeconfig)
    follow_logs.logs(name, tail)


@app.command()
def utilization_chart(out: Path = Path("output/utilization.png"), title: str = "HAMi GPU allocation") -> None:
    """Draw the current per-GPU memory allocation from HAMi annotations."""
    gpus = utilization.collect()
    for g in gpus:
        typer.echo(f"{g.node} {g.uuid} {g.mode}: " + ", ".join(f"{s.pod} {s.kind} {s.mem}MiB" for s in g.slices))
    typer.echo(utilization.draw(gpus, out, title))
