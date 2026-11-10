"""Thin wrappers around kubectl, helmfile and ssh, with presenter-friendly colour output."""

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

ROOT = Path(__file__).resolve().parents[2]
console = Console(highlight=False)

# Pod-spec fields worth showing on stage; the rest of a manifest is plumbing.
CONTAINER_FIELDS = ("name", "image", "command", "args", "resources")
POD_FIELDS = ("schedulerName", "nodeSelector", "nodeName")


def show_cmd(cmd: tuple[str, ...] | list[str]) -> None:
    console.print(Text("$ ", style="bold green") + Text(shlex.join(cmd), style="bold cyan"))


def sh(*cmd: str, input: str | None = None, check: bool = True, cwd: Path | None = None, env: dict | None = None, quiet: bool = False) -> str:
    if not quiet:
        show_cmd(cmd)
    r = subprocess.run(cmd, check=False, input=input, text=True, capture_output=True, cwd=cwd, env={**os.environ, **(env or {})})
    if check and r.returncode:
        console.print(r.stderr.strip() or r.stdout.strip(), style="red")
        raise typer.Exit(r.returncode)
    return r.stdout


def kubectl(*args: str, **kw) -> str:
    return sh("kubectl", *args, **kw)


def kjson(*args: str) -> dict:
    """Read-only lookups (polling, charts) stay quiet so the terminal only shows the steps that matter."""
    return json.loads(kubectl("get", *args, "-o", "json", quiet=True))


def relevant(doc: dict) -> dict:
    """kind/name plus the scheduling-relevant parts: pod annotations, scheduler, containers' image/command/resources."""
    out = {"kind": doc["kind"], "name": doc["metadata"]["name"]}
    tmpl = doc.get("spec", {}).get("template", doc)  # Deployment/Job template, or the Pod itself
    if ann := tmpl.get("metadata", {}).get("annotations"):
        out["annotations"] = ann
    spec = tmpl.get("spec", {})
    out |= {k: spec[k] for k in POD_FIELDS if k in spec}
    if cs := spec.get("containers"):
        out["containers"] = [{k: c[k] for k in CONTAINER_FIELDS if k in c} for c in cs]
    return out


def show_manifest(text: str) -> None:
    for doc in filter(None, yaml.safe_load_all(text)):
        r = relevant(doc)
        if len(r) == 2:  # Service, ConfigMap, ...: one line is enough
            console.print(f"  {r['kind']}/{r['name']}", style="dim")
        else:
            # Flow style for leaf lists/maps keeps command, args and limits on one line each.
            console.print(Syntax("---\n" + yaml.safe_dump(r, sort_keys=False, width=200, default_flow_style=None).rstrip(), "yaml", theme="monokai", background_color="default"))


def apply(text: str, namespace: str = "default", delete: bool = False) -> None:
    """kubectl apply (or delete) manifests given as text; on apply, show the parts that matter first."""
    if not delete:
        show_manifest(text)
    verb = ["delete", "--wait", "--ignore-not-found"] if delete else ["apply"]
    kubectl("-n", namespace, *verb, "-f", "-", input=text)


def render(path: Path, values: dict[str, str]) -> str:
    text = path.read_text()
    for k, v in values.items():
        text = text.replace("${" + k + "}", v)
    return text


def wait_until(check, timeout: int, every: int = 10, what: str = "condition"):
    end = time.time() + timeout
    with console.status(f"waiting for {what}"):
        while time.time() < end:
            if result := check():
                return result
            time.sleep(every)
    console.print(f"timed out waiting for {what}", style="red")
    raise typer.Exit(1)
