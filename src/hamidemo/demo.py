"""Demos are described in demos/<name>/demo.yaml; this module runs them."""

import json
import os
from datetime import datetime
from pathlib import Path

import yaml

from . import env as envs
from . import perf, utilization
from .kube import ROOT, apply, console, kjson, kubectl, render, wait_until


def load(name: str) -> tuple[Path, dict]:
    d = ROOT / "demos" / name
    return d, yaml.safe_load((d / "demo.yaml").read_text())


def names() -> list[str]:
    return sorted(p.parent.name for p in (ROOT / "demos").glob("*/demo.yaml"))


def selectors(spec: dict) -> list[str]:
    """Label selectors for every workload a demo starts, in phase order, plus any extra `logs:` selectors.
    perf-* Jobs label their pods job-name=perf-<target>."""
    out = list(spec.get("logs", []))
    for p in spec["phases"]:
        if sel := p.get("selector"):
            out.append(sel)
        else:
            out += [f"app={d}" for d in p.get("deployments", [])]
        if pod := p.get("pod"):
            out.append(f"app={pod}")
        out += [f"job-name=perf-{t['name']}" for t in p.get("bench", {}).get("targets", [])]
    return list(dict.fromkeys(out))


class Run:
    def __init__(self, demo: str, benchmark: bool = True, live: bool = True):
        self.dir, self.spec = load(demo)
        self.benchmark, self.live = benchmark, live
        self.ns = self.spec.get("namespace", "default")
        self.out = ROOT / "output" / demo / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.out.mkdir(parents=True)
        # Demo values: environment variables win, then config.toml node roles, then demo.yaml defaults.
        roles = envs.node_values(self.spec["environment"]) if self.spec.get("environment") != "mock" else {}
        self.values = {k: os.environ.get(k, roles.get(k, str(v))) for k, v in self.spec.get("values", {}).items()}
        self.charts = 0

    def log(self, title: str, text: str = "") -> None:
        block = f"\n==> {title}\n{text.rstrip()}\n" if text else f"\n==> {title}\n"
        console.print(f"\n==> {title}", style="bold magenta")
        if text:
            # crop, not wrap: nvidia-smi tables are 89 columns and must keep their borders
            console.print(text.rstrip(), markup=False, overflow="ignore", crop=False, no_wrap=True)
        with open(self.out / "run.log", "a") as f:
            f.write(block)

    def png(self, title: str) -> Path:
        self.charts += 1
        return self.out / f"{self.charts:02d}-{title.lower().replace(' ', '-')}.png"

    def snapshot(self, title: str) -> None:
        self.log(f"Allocation chart: {utilization.draw(utilization.collect(), self.png(title), title).relative_to(ROOT)}")

    def k(self, *args: str, **kw) -> str:
        return kubectl("-n", self.ns, *args, **kw)

    def completion(self, url: str, model: str) -> str:
        body = json.dumps({"model": model, "prompt": "The capital of France is", "max_tokens": 32, "temperature": 0})
        # Unique name: a leftover pod from an interrupted run must not block the next one.
        return self.k("run", f"completion-check-{os.getpid()}-{self.charts}", "--rm", "-i", "--restart=Never", "--image=curlimages/curl:8.11.1", "--quiet", "--",
                      "-fsS", f"{url}/v1/completions", "-H", "Content-Type: application/json", "-d", body)

    def bench(self, b: dict, title: str) -> None:
        """Same inference-perf load against every target, one after the other, then compare."""
        config = yaml.safe_load((self.dir / b["config"]).read_text())
        qps = [s["rate"] for s in config["load"]["stages"]]
        results = {}
        for t in b["targets"]:
            self.log(f"inference-perf against {t['name']} ({t['url']})")
            results[t["name"]] = reports = perf.run(f"perf-{t['name']}", config, t, self.ns, b.get("timeout", 3600))
            (self.out / f"perf-{t['name']}.json").write_text(json.dumps(reports, indent=1))
        self.log(f"Benchmark: {title}", perf.summary(results, qps))
        self.log(f"Benchmark chart: {perf.chart(results, qps, self.png(title + ' latency'), title).relative_to(ROOT)}")

    def phase(self, p: dict, keep: bool) -> None:
        self.log(p["name"])
        manifest = render(self.dir / p["manifest"], self.values) if "manifest" in p else None
        # Stream events and container logs from before the apply, so scheduling and pulls show up right away.
        sels = selectors({"phases": [{**p, "bench": {}}]})
        stream = None
        if self.live and sels:
            from .logs import Stream  # logs imports demo

            stream = Stream(sels, self.ns, tail=5)
        try:
            if manifest:
                apply(manifest, self.ns)
            for d, n in p.get("scale", {}).items():
                self.k("scale", f"deploy/{d}", f"--replicas={n}")
            for d in [*p.get("deployments", []), *p.get("scale", {})]:
                self.k("rollout", "status", f"deploy/{d}", f"--timeout={p.get('timeout', 1800)}s")
            if pod := p.get("pod"):
                wait_until(lambda: kjson("-n", self.ns, "pod", pod)["status"]["phase"] in ("Succeeded", "Failed"), p.get("timeout", 600), what=pod)
        finally:
            if stream:
                stream.stop()
        if (pod := p.get("pod")) and not stream:
            self.log(f"{pod} logs", self.k("logs", pod))
        if sel := p.get("selector"):
            self.log("Pods", self.k("get", "pods", "-l", sel, "-o", "wide"))
            for pod_name in self.k("get", "pods", "-l", sel, "-o", "name").split():
                if mig := kjson("-n", self.ns, pod_name)["metadata"].get("annotations", {}).get(utilization.MIG_ALLOCATIONS):
                    self.log(f"{pod_name} MIG reservation", json.dumps(json.loads(mig), indent=1))
                for cmd in p.get("exec", []):
                    self.log(f"{pod_name}: {cmd}", self.k("exec", pod_name, "--", *cmd.split(), check=False))
        self.snapshot(p["name"])
        for t in p.get("check", []) or p.get("bench", {}).get("targets", []):
            self.log(f"Completion via {t['name']}", self.completion(t["url"], t["model"]))
        if (b := p.get("bench")) and self.benchmark:
            self.bench(b, p["name"])
        if not keep and self.spec.get("teardown") != "end":
            self.teardown(p)

    def teardown(self, p: dict) -> None:
        if "manifest" in p:
            apply(render(self.dir / p["manifest"], self.values), self.ns, delete=True)
        for d in p.get("scale", {}):
            self.k("scale", f"deploy/{d}", "--replicas=0")

    def __call__(self, keep: bool) -> Path:
        self.log(self.spec["title"], self.spec.get("description", ""))
        if self.benchmark:
            self.snapshot("Before")
        for p in self.spec["phases"]:
            self.phase(p, keep)
        if not keep:
            if self.spec.get("teardown") == "end":
                for p in reversed(self.spec["phases"]):
                    self.teardown(p)
            self.snapshot("After")
        return self.out
