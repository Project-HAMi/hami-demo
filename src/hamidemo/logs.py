"""Follow a demo's workload logs in a second terminal while `hamidemo run` drives the demo in the first."""

import re
import subprocess
import threading
import time

from rich.text import Text

from . import demo
from .kube import console, show_cmd

COLOURS = ("cyan", "yellow", "green", "magenta", "blue", "red")


def follow(selector: str, ns: str, colour: str, tail: int) -> None:
    """kubectl logs -f until the pods go away, then wait for the next ones: demos create and delete workloads per phase."""
    cmd = ["kubectl", "-n", ns, "logs", "-f", "-l", selector, "--all-containers", "--prefix", f"--tail={tail}", "--max-log-requests=20", "--ignore-errors"]
    shown = False
    while True:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if not shown:
            show_cmd(cmd)
            shown = True
        for line in p.stdout:
            if line.startswith("No resources found") or "is waiting to start" in line:
                continue
            # kubectl --prefix gives "[pod/<name>/<container>] "; keep just the pod name.
            m = re.match(r"\[pod/([^/\]]+)/[^\]]*\] ?", line)
            who, line = (m[1], line[m.end():]) if m else (selector, line)
            console.print(Text.assemble((f"{who} ", f"bold {colour}"), line.rstrip()))
        p.wait()
        time.sleep(3)


def logs(name: str, tail: int) -> None:
    _, spec = demo.load(name)
    ns = spec.get("namespace", "default")
    sels = demo.selectors(spec)
    console.print(f"Following {name} in namespace {ns}; Ctrl-C to stop", style="bold magenta")
    for i, sel in enumerate(sels):
        threading.Thread(target=follow, args=(sel, ns, COLOURS[i % len(COLOURS)], tail), daemon=True).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
