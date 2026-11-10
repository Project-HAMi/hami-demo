"""Demos are described in demos/<name>/demo.yaml; this module runs them."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml

from . import utilization
from .kube import ROOT, apply, kjson, kubectl, wait_until

BENCH_IMAGE = "vllm/vllm-openai:v0.25.1-ubuntu2404"


def load(name: str) -> tuple[Path, dict]:
    d = ROOT / "demos" / name
    return d, yaml.safe_load((d / "demo.yaml").read_text())


def names() -> list[str]:
    return sorted(p.parent.name for p in (ROOT / "demos").glob("*/demo.yaml"))


class Run:
    def __init__(self, demo: str):
        self.dir, self.spec = load(demo)
        self.out = ROOT / "output" / demo / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.out.mkdir(parents=True)
        self.values = {k: os.environ.get(k, str(v)) for k, v in self.spec.get("values", {}).items()}
        self.charts = 0

    def log(self, title: str, text: str = "") -> None:
        block = f"\n==> {title}\n{text.rstrip()}\n" if text else f"\n==> {title}\n"
        print(block, end="")
        with open(self.out / "run.log", "a") as f:
            f.write(block)

    def snapshot(self, title: str) -> None:
        self.charts += 1
        png = utilization.draw(utilization.collect(), self.out / f"{self.charts:02d}-{title.lower().replace(' ', '-')}.png", title)
        self.log(f"Allocation chart: {png.relative_to(ROOT)}")

    def completion(self, svc: str, model: str) -> str:
        body = json.dumps({"model": model, "prompt": "The capital of France is", "max_tokens": 32, "temperature": 0})
        return kubectl("run", f"curl-{svc}", "--rm", "-i", "--restart=Never", "--image=curlimages/curl:8.11.1", "--quiet", "--",
                       "-fsS", f"http://{svc}:8000/v1/completions", "-H", "Content-Type: application/json", "-d", body)

    def bench(self, svc: str, model: str, concurrency: int, prompts: int) -> str:
        out = kubectl("run", f"bench-{svc}", "--rm", "-i", "--restart=Never", f"--image={BENCH_IMAGE}", "--quiet", "--command", "--",
                      "vllm", "bench", "serve", "--backend", "openai", "--base-url", f"http://{svc}:8000", "--model", model,
                      "--dataset-name", "random", "--random-input-len", "512", "--random-output-len", "128",
                      "--num-prompts", str(prompts), "--max-concurrency", str(concurrency), "--percentile-metrics", "ttft,tpot,e2el")
        (self.out / f"bench-{svc}.txt").write_text(out)
        keep = ("Successful requests", "Output token throughput", "Mean TTFT", "P99 TTFT", "Mean TPOT")
        return "\n".join(l for l in out.splitlines() if l.startswith(keep))

    def phase(self, p: dict, keep: bool) -> None:
        manifest = self.dir / p["manifest"]
        self.log(p["name"])
        apply(manifest, self.values)
        for d in p.get("deployments", []):
            kubectl("rollout", "status", f"deploy/{d}", f"--timeout={p.get('timeout', 1200)}s")
        if pod := p.get("pod"):
            wait_until(lambda: kjson("pod", pod)["status"]["phase"] in ("Succeeded", "Failed"), p.get("timeout", 600), what=pod)
            self.log(f"{pod} logs", kubectl("logs", pod))
        sel = p.get("selector")
        if sel:
            self.log("Pods", kubectl("get", "pods", "-l", sel, "-o", "wide"))
            for pod_name in kubectl("get", "pods", "-l", sel, "-o", "name").split():
                ann = kjson(pod_name)["metadata"].get("annotations", {})
                if mig := ann.get(utilization.MIG_ALLOCATIONS):
                    self.log(f"{pod_name} MIG reservation", json.dumps(json.loads(mig), indent=1))
                for cmd in p.get("exec", []):
                    self.log(f"{pod_name}: {cmd}", kubectl("exec", pod_name, "--", *cmd.split(), check=False))
        self.snapshot(p["name"])
        services = p.get("services", [])
        for s in services:
            self.log(f"Completion from {s['name']}", self.completion(s["name"], s["model"]))
        if b := p.get("bench"):
            with ThreadPoolExecutor(len(services)) as ex:
                results = ex.map(lambda s: (s["name"], self.bench(s["name"], s["model"], b["concurrency"], b["prompts"])), services)
                for name, summary in results:
                    self.log(f"Benchmark {name}", summary)
        if not keep:
            apply(manifest, self.values, delete=True)

    def __call__(self, keep: bool) -> Path:
        self.log(self.spec["title"], self.spec.get("description", ""))
        self.snapshot("Before")
        for p in self.spec["phases"]:
            self.phase(p, keep)
        if not keep:
            self.snapshot("After")
        return self.out
