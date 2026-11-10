"""Stream a demo's pod events and container logs: in a second terminal (`hamidemo logs`) or inline while `run` waits."""

import re
import subprocess
import threading
import time

from rich.text import Text

from . import demo
from .kube import console, show_cmd

COLOURS = ("cyan", "yellow", "green", "magenta", "blue", "red")


class Stream:
    """kubectl logs -f per selector plus pod events, restarted as pods come and go, until stop()."""

    def __init__(self, selectors: list[str], ns: str, tail: int = 20, events: bool = True):
        self.ns, self.tail, self.done, self.procs = ns, tail, threading.Event(), []
        jobs = [(self.follow, (sel, COLOURS[i % len(COLOURS)])) for i, sel in enumerate(selectors)]
        if events:
            jobs.append((self.events, ()))
        for fn, args in jobs:
            threading.Thread(target=fn, args=args, daemon=True).start()

    def lines(self, cmd: list[str]):
        while not self.done.is_set():
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.procs.append(p)
            yield from p.stdout
            p.wait()
            self.done.wait(3)

    def follow(self, selector: str, colour: str) -> None:
        cmd = ["kubectl", "-n", self.ns, "logs", "-f", "-l", selector, "--all-containers", "--prefix", f"--tail={self.tail}", "--max-log-requests=20", "--ignore-errors"]
        show_cmd(cmd)
        for line in self.lines(cmd):
            if line.startswith("No resources found") or "is waiting to start" in line:
                continue  # not started yet; the events stream shows why
            # kubectl --prefix gives "[pod/<name>/<container>] "; keep just the pod name.
            m = re.match(r"\[pod/([^/\]]+)/[^\]]*\] ?", line)
            who, line = (m[1], line[m.end():]) if m else (selector, line)
            console.print(Text.assemble((f"{who} ", f"bold {colour}"), line.rstrip()))

    def events(self) -> None:
        """Scheduling, MIG/slice allocation, image pulls: what happens before a container has logs."""
        cmd = ["kubectl", "-n", self.ns, "get", "events", "-w", "--watch-only", "--field-selector", "involvedObject.kind=Pod",
               "-o", "custom-columns=POD:.involvedObject.name,REASON:.reason,MESSAGE:.message", "--no-headers"]
        show_cmd(cmd)
        for line in self.lines(cmd):
            pod, _, rest = line.strip().partition(" ")
            reason, _, msg = rest.strip().partition(" ")
            if reason == "Unhealthy" and "connection refused" in msg:
                continue  # probes before the server listens: expected during model load
            style = "bold red" if reason in ("Failed", "FailedScheduling", "BackOff", "FailedMount") else "bold white"
            console.print(Text.assemble((f"{pod} ", "dim"), (f"{reason} ", style), msg.strip()))

    def stop(self) -> None:
        self.done.set()
        for p in self.procs:
            p.kill()


def logs(name: str, tail: int) -> None:
    _, spec = demo.load(name)
    ns = spec.get("namespace", "default")
    console.print(f"Following {name} in namespace {ns}; Ctrl-C to stop", style="bold magenta")
    Stream(demo.selectors(spec), ns, tail)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
