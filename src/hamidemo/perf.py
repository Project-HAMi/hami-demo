"""Run inference-perf Jobs and compare TTFT, TPOT, latency and throughput per load stage."""

import json
import re
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
import yaml

from .kube import apply, kjson, kubectl, wait_until

IMAGE = "quay.io/inference-perf/inference-perf:latest"
# Reports go to stdout so they can be read after the pod ends.
SCRIPT = ("inference-perf --config_file /cfg/config.yml && find /out -name '*lifecycle_metrics.json' ! -name 'per_request*' "
          "-exec sh -c 'echo \"@@@ $(basename $1)\"; cat \"$1\"; echo' _ {} \\;")
METRICS = {
    "TTFT": ("latency", "time_to_first_token"),
    "TPOT": ("latency", "time_per_output_token"),
    "E2E latency": ("latency", "request_latency"),
}


def manifests(name: str, cfg: dict) -> str:
    meta = {"name": name}
    cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta, "data": {"config.yml": yaml.safe_dump(cfg, sort_keys=False)}}
    pod = {"restartPolicy": "Never", "volumes": [{"name": "cfg", "configMap": {"name": name}}], "containers": [{
        "name": "inference-perf", "image": IMAGE, "command": ["sh", "-c", SCRIPT],
        "volumeMounts": [{"name": "cfg", "mountPath": "/cfg"}], "resources": {"requests": {"cpu": "2", "memory": "4Gi"}}}]}
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": meta, "spec": {"backoffLimit": 0, "template": {"spec": pod}}}
    return yaml.safe_dump_all([cm, job])


def run(name: str, config: dict, target: dict, namespace: str, timeout: int) -> dict[str, dict]:
    """Run one inference-perf Job against target {url, model?}; return {report file: parsed JSON}."""
    server = {**config["server"], "base_url": target["url"], **({"model_name": target["model"]} if "model" in target else {})}
    tokenizer = {"pretrained_model_name_or_path": server["model_name"]}
    cfg = {**config, "server": server, "tokenizer": tokenizer, "storage": {"local_storage": {"path": "/out"}}}
    docs = manifests(name, cfg)
    apply(docs, namespace, delete=True)
    apply(docs, namespace)
    done = lambda: (s := kjson("-n", namespace, "job", name).get("status", {})).get("succeeded") or s.get("failed")
    wait_until(done, timeout, every=15, what=f"job {name}")
    logs = kubectl("-n", namespace, "logs", f"job/{name}")
    apply(docs, namespace, delete=True)
    reports = {m.group(1): json.loads(m.group(2)) for m in re.finditer(r"@@@ (\S+)\n(\{.*?\n\})\n", logs, re.DOTALL)}
    if not reports:
        raise SystemExit(f"{name}: no reports in the job log\n{logs[-2000:]}")
    return reports


def stages(reports: dict[str, dict], qps: list[float]) -> list[tuple[str, dict]]:
    found = sorted((int(re.search(r"stage_(\d+)", f).group(1)), r) for f, r in reports.items() if f.startswith("stage_"))
    return [(f"{qps[i]} QPS", r) for i, r in found] + [("all", reports["summary_lifecycle_metrics.json"])]


def metric(report: dict, path: tuple[str, str], stat: str) -> float:
    v = report.get("successes", {}).get(path[0], {}).get(path[1], {})
    return (v.get(stat) if isinstance(v, dict) else None) or 0.0


def summary(results: dict[str, dict], qps: list[float]) -> str:
    rows = []
    for target, reports in results.items():
        for label, r in stages(reports, qps):
            ok, failed = r.get("successes", {}), r.get("failures", {})
            vals = " | ".join(f"{k} p50 {metric(r, p, 'median'):.3f}s p90 {metric(r, p, 'p90'):.3f}s" for k, p in METRICS.items())
            tput = ok.get("throughput", {}).get("output_tokens_per_sec", 0)
            rows.append(f"{target:16} {label:9} {vals} | {tput:.0f} out tok/s | {ok.get('count', 0)} ok, {failed.get('count', 0)} failed")
    return "\n".join(rows)


def chart(results: dict[str, dict], qps: list[float], out: Path, title: str) -> Path:
    sns.set_theme(style="whitegrid", context="talk")
    fig, axes = plt.subplots(1, len(METRICS), figsize=(7 * len(METRICS), 6))
    for ax, (name, path) in zip(axes, METRICS.items()):
        for target, reports in results.items():
            st = [r for label, r in stages(reports, qps) if label != "all"]
            ax.plot(qps[: len(st)], [metric(r, path, "median") for r in st], marker="o", label=f"{target} p50")
            ax.plot(qps[: len(st)], [metric(r, path, "p90") for r in st], marker="o", linestyle="--", label=f"{target} p90")
        ax.set(title=name, xlabel="offered QPS", ylabel="seconds")
    axes[0].legend(fontsize=11)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out
