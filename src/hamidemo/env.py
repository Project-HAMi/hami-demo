"""Environments: environment.yaml is the reusable recipe, config.toml says where it runs, helmfile applies the releases."""

import json
import os
import subprocess
import tempfile
import tomllib
from pathlib import Path

import yaml

from .kube import ROOT, kubectl, sh

FORWARD_PORT = 6443


def config() -> dict:
    """Site topology from config.toml (or $HAMIDEMO_CONFIG); see config.toml.example."""
    path = Path(os.environ.get("HAMIDEMO_CONFIG", ROOT / "config.toml"))
    return tomllib.loads(path.read_text()) if path.exists() else {}


def merge(base: dict, over: dict) -> dict:
    return {**base, **{k: merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v for k, v in over.items()}}


def load(name: str) -> dict:
    """Recipe from environments/<name>/environment.yaml, overlaid with [environments.<name>] from config.toml."""
    recipe = yaml.safe_load((ROOT / "environments" / name / "environment.yaml").read_text())
    cfg = config()
    site = cfg.get("environments", {}).get(name, {})
    if recipe.get("needs_config") and not site.get("hosts"):
        raise SystemExit(f"environment {name} needs [environments.{name}] in config.toml; copy config.toml.example")
    return merge({"ssh_key": cfg.get("ssh_key", "~/.ssh/id_rsa")}, merge(recipe, site))


def node_values(name: str) -> dict[str, str]:
    """Role -> node name; exported to helmfile and used as ${NAME} defaults in demo manifests."""
    return {k: str(v) for k, v in load(name).get("nodes", {}).items()}


def names() -> list[str]:
    return sorted(p.parent.name for p in (ROOT / "environments").glob("*/environment.yaml"))


def ssh(spec: dict, host: str, *args: str, cmd: str = "") -> str:
    key = os.path.expanduser(spec["ssh_key"])
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
    for role, cmd in spec.get("prepare", {}).items():
        # Keys are node roles (MIG_NODE) resolved via [nodes], or host keys directly.
        print(ssh(spec, spec.get("nodes", {}).get(role, role), cmd=cmd))
    sh("helmfile", "-e", name, "apply", "--skip-diff-on-install", cwd=ROOT / "environments", env=node_values(name))
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
    key = os.path.expanduser(spec["ssh_key"])
    return ["ssh", "-i", key, "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-D", f"127.0.0.1:{port}", spec["hosts"][spec["control_plane"]]]


def proxy_pattern(name: str) -> str:
    """Matches only the proxy's own ssh command line (anchored, so shells mentioning it don't match)."""
    return rf"^ssh .*-D 127\.0\.0\.1:{load(name).get('local_api_port', FORWARD_PORT)} "


def proxy_running(name: str) -> bool:
    return subprocess.run(["pgrep", "-f", "--", proxy_pattern(name)], check=False, capture_output=True).returncode == 0


def connect(name: str) -> Path:
    """Kubeconfig plus the SOCKS proxy in the background (restarted if already running)."""
    out = kubeconfig(name)
    args = proxy_args(name)
    subprocess.run(["pkill", "-f", "--", proxy_pattern(name)], check=False)
    sh(*args[:-1], "-f", args[-1])
    return out


def use(name: str) -> Path:
    """Point this process at environment <name>: its kubeconfig, with the proxy started if it is down.
    Remote environments only; mock keeps the caller's KUBECONFIG (local kind)."""
    spec = load(name)
    if "hosts" not in spec:
        return Path(os.environ.get("KUBECONFIG", "~/.kube/config"))
    path = ROOT / "environments" / name / "kubeconfig"
    if not (path.exists() and proxy_running(name)):
        connect(name)
    os.environ["KUBECONFIG"] = str(path)
    return path
