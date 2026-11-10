"""Environments: environment.yaml for access and node prep, environments/helmfile.yaml.gotmpl for releases."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from .kube import ROOT, kubectl, sh

FORWARD_PORT = 6443


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / "environments" / name / "environment.yaml").read_text())


def names() -> list[str]:
    return sorted(p.parent.name for p in (ROOT / "environments").glob("*/environment.yaml"))


def ssh(spec: dict, host: str, *args: str, cmd: str = "") -> str:
    key = os.path.expanduser(spec.get("ssh_key", "~/.ssh/id_kubecon"))
    return sh("ssh", "-i", key, "-o", "BatchMode=yes", *args, spec["hosts"][host], *([cmd] if cmd else []))


def kind_cluster(spec: dict) -> None:
    k = spec["kind"]
    if k["name"] in sh("kind", "get", "clusters").split():
        return
    node = {"role": "control-plane", **({"extraMounts": k["extraMounts"]} if "extraMounts" in k else {})}
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump({"kind": "Cluster", "apiVersion": "kind.x-k8s.io/v1alpha4", "name": k["name"], "nodes": [node]}, f)
        f.flush()
        sh("kind", "create", "cluster", "--config", f.name, "--wait", "120s")


def patch_node(name: str, spec: dict) -> None:
    node = kubectl("get", "nodes", "-o", "name").split()[0]
    for k, v in spec.get("node_labels", {}).items():
        kubectl("label", node, f"{k}={v}", "--overwrite")
    p = spec.get("node_patch", {})
    for k, v in p.get("capacity", {}).items():
        op = [{"op": "add", "path": "/status/capacity/" + k.replace("/", "~1"), "value": v}]
        kubectl("patch", node, "--subresource=status", "--type=json", "-p", json.dumps(op))
    for k, f in p.get("annotations", {}).items():
        kubectl("annotate", node, "--overwrite", f"{k}={(ROOT / 'environments' / name / f).read_text().strip()}")


def setup(name: str, remove_conflicts: bool) -> None:
    spec = load(name)
    if "kind" in spec:
        kind_cluster(spec)
        sh("kubectl", "config", "use-context", f"kind-{spec['kind']['name']}")
    for ds, release in spec.get("conflicts", {}).items():
        ns, ds_name = ds.split("/")
        if kubectl("-n", ns, "get", "ds", ds_name, "--ignore-not-found", "-o", "name").strip():
            if not remove_conflicts:
                raise SystemExit(f"{ds} also serves nvidia.com/gpu; rerun with --remove-conflicts")
            sh("helm", "uninstall", release, "-n", ns, "--wait")
    for host, cmd in spec.get("prepare", {}).items():
        print(ssh(spec, host, cmd=cmd))
    sh("helmfile", "-e", name, "apply", "--skip-diff-on-install", cwd=ROOT / "environments", env=spec.get("env", {}))
    patch_node(name, spec)


def connect(name: str) -> Path:
    """Copy the kubeconfig over SSH and tunnel the API server to localhost."""
    spec = load(name)
    cp = spec["control_plane"]
    raw = ssh(spec, cp, cmd=f"kind get kubeconfig --name {spec['kind']['name']}" if "kind" in spec else "kubectl config view --raw --minify")
    cfg = yaml.safe_load(raw)
    cluster = cfg["clusters"][0]["cluster"]
    remote = cluster["server"].split("//")[1]
    if remote.startswith(("0.0.0.0", "127.0.0.1")):
        remote = remote.replace("0.0.0.0", "127.0.0.1")
    cluster["server"] = f"https://127.0.0.1:{FORWARD_PORT}"
    cluster["tls-server-name"] = "kubernetes"
    out = ROOT / "environments" / name / "kubeconfig"
    out.write_text(yaml.safe_dump(cfg))
    out.chmod(0o600)
    forward = f"{FORWARD_PORT}:{remote}"
    subprocess.run(["pkill", "-f", f"-L {FORWARD_PORT}:"], check=False)
    ssh(spec, cp, "-fNT", "-o", "ExitOnForwardFailure=yes", "-L", forward)
    return out
