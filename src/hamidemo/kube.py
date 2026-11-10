"""Thin wrappers around kubectl, helmfile and ssh."""

import json
import os
import subprocess
import time
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[2]


def sh(*cmd: str, input: str | None = None, check: bool = True, cwd: Path | None = None, env: dict | None = None) -> str:
    typer.secho("$ " + " ".join(cmd), dim=True)
    r = subprocess.run(cmd, check=False, input=input, text=True, capture_output=True, cwd=cwd, env={**os.environ, **(env or {})})
    if check and r.returncode:
        raise typer.Exit(typer.secho(r.stderr.strip() or r.stdout.strip(), fg="red") or r.returncode)
    return r.stdout


def kubectl(*args: str, **kw) -> str:
    return sh("kubectl", *args, **kw)


def kjson(*args: str) -> dict:
    return json.loads(kubectl("get", *args, "-o", "json"))


def apply(text: str, namespace: str = "default", delete: bool = False) -> None:
    """kubectl apply (or delete) manifests given as text."""
    verb = ["delete", "--wait", "--ignore-not-found"] if delete else ["apply"]
    kubectl("-n", namespace, *verb, "-f", "-", input=text)


def render(path: Path, values: dict[str, str]) -> str:
    text = path.read_text()
    for k, v in values.items():
        text = text.replace("${" + k + "}", v)
    return text


def wait_until(check, timeout: int, every: int = 10, what: str = "condition"):
    end = time.time() + timeout
    while time.time() < end:
        if result := check():
            return result
        time.sleep(every)
    raise typer.Exit(typer.secho(f"timed out waiting for {what}", fg="red") or 1)
