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
    if k["name"] not in sh("kind", "get", "clusters").split():
        node = {"role": "control-plane", **({"extraMounts": k["extraMounts"]} if "extraMounts" in k else {})}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            yaml.safe_dump({"kind": "Cluster", "apiVersion": "kind.x-k8s.io/v1alpha4", "name": k["name"], "nodes": [node]}, f)
            f.flush()
            sh("kind", "create", "cluster", "--config", f.name, "--wait", "120s")
    # Hosts with arp_ignore=2 (see CAVEATS.md) break pod-to-gateway ARP inside the kind node.
    if sysctls := k.get("node_sysctls"):
        script = "; ".join(f"for f in /proc/sys/{path}; do echo {v} > $f; done" for path, v in sysctls.items())
        sh("docker", "exec", f"{k['name']}-control-plane", "sh", "-c", script)
        kubectl("delete", "pods", "-A", "--field-selector=status.phase!=Running", "--ignore-not-found", check=False)


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


def kubeconfig(name: str, out: Path | None = None) -> Path:
    """Copy the kubeconfig over SSH. API traffic goes through a SOCKS proxy on the control plane
    (`proxy-url`), so the server address and certificate stay as the cluster issued them."""
    spec = load(name)
    raw = ssh(spec, spec["control_plane"], cmd=f"kind get kubeconfig --name {spec['kind']['name']}" if "kind" in spec else "kubectl config view --raw --minify")
    cfg = yaml.safe_load(raw)
    ctx = f"hamidemo-{name}"
    cfg["clusters"][0]["cluster"]["proxy-url"] = f"socks5://127.0.0.1:{spec.get('local_api_port', FORWARD_PORT)}"
    cfg["clusters"][0]["name"] = cfg["contexts"][0]["context"]["cluster"] = ctx
    cfg["contexts"][0]["name"] = cfg["current-context"] = ctx
    out = out or ROOT / "environments" / name / "kubeconfig"
    out.write_text(yaml.safe_dump(cfg))
    out.chmod(0o600)
    return out


def proxy_args(name: str) -> list[str]:
    spec = load(name)
    port = spec.get("local_api_port", FORWARD_PORT)
    key = os.path.expanduser(spec.get("ssh_key", "~/.ssh/id_kubecon"))
    return ["ssh", "-i", key, "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-D", f"127.0.0.1:{port}", spec["hosts"][spec["control_plane"]]]


def connect(name: str) -> Path:
    """Kubeconfig plus the SOCKS proxy in the background."""
    out = kubeconfig(name)
    args = proxy_args(name)
    subprocess.run(["pkill", "-f", " ".join(args[-3:])], check=False)
    sh(*args[:-1], "-f", args[-1])
    return out
