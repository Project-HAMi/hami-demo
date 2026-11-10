# HAMi KubeCon Demos

Reproducible HAMi demos on three environments, driven by one Python CLI, `hamidemo`:

- **vllm-mig**: two vLLM servers on dynamically created MIG instances (HAMi Flexible MIG)
- **remote-cuda**: CUDA and vLLM on GPUs from another node over HAMi Remote GPU (Lupine)
- **epp-burst**: llm-d EPP cache-aware routing against random routing under a shared-prefix burst, measured as TTFT
- **vllm-amd-share**: two standard vLLM ROCm servers sharing one AMD Instinct MI300X through HAMi slices

Every demo uses the same benchmark path ([inference-perf](https://github.com/kubernetes-sigs/inference-perf)) and writes its raw reports, a TTFT / TPOT / latency chart per QPS stage and per-GPU allocation charts to `output/<demo>/<timestamp>/`.

## Quick start

Needs `uv`, `kubectl`, `helm`, `helmfile` and `ssh`.

```bash
cp config.toml.example config.toml                 # once: your hosts, SSH key and node roles
uv run hamidemo env list
uv run hamidemo env connect a100                    # kubeconfig + SOCKS proxy; prints the export
export KUBECONFIG=$PWD/environments/a100/kubeconfig
uv run hamidemo env setup a100 --remove-conflicts   # once: installs HAMi 2.11.0, replaces the Volcano vGPU plugin
uv run hamidemo list
uv run hamidemo run vllm-mig
uv run hamidemo run remote-cuda
uv run hamidemo run epp-burst
uv run hamidemo run vllm-amd-share                  # amd environment
uv run hamidemo utilization-chart                   # current allocation on any HAMi cluster
```

### Presenting

Two terminals, same `KUBECONFIG`:

```bash
uv run hamidemo logs vllm-mig    # terminal 2: start first; follows every workload of the demo
uv run hamidemo run vllm-mig     # terminal 1: drives the demo
```

`run` prints each `kubectl` / `helm` command before it runs, and before every apply the parts of the manifest that matter (annotations, scheduler, image, command, GPU resources) as highlighted YAML. Status polling stays quiet behind a spinner. `logs` runs `kubectl logs -f` per workload with a coloured pod-name prefix and picks up new pods as the demo moves through its phases; `--tail N` sets the history shown on attach.

### Configuration

`config.toml` (git-ignored; template [config.toml.example](config.toml.example)) holds the site topology: SSH key, hosts per environment, control plane, local proxy port, kind cluster name and node roles (`MIG_NODE`, `REMOTE_SERVER_NODE`, `CLIENT_NODE`). Roles are exported to helmfile and substituted as `${ROLE}` in demo manifests; an environment variable of the same name overrides them for one run. `environments/<name>/environment.yaml` stays the reusable recipe (kind mounts, sysctls, conflicting plugins, node preparation). `HAMIDEMO_CONFIG=/path/to/other.toml` selects another topology.

### Debug a remote cluster locally

```bash
uv run hamidemo env connect a100     # pull kubeconfig + SOCKS proxy in the background (127.0.0.1:6443)
uv run hamidemo env connect amd      # same for amd (127.0.0.1:6444); both can run at once
export KUBECONFIG=environments/amd/kubeconfig
kubectl get pods -A                  # helm and k9s work the same way

uv run hamidemo env kubeconfig amd --out ~/.kube/hamidemo-amd   # only the kubeconfig (context hamidemo-amd)
uv run hamidemo env proxy amd                                   # proxy in the foreground, Ctrl-C to stop
```

The kubeconfig keeps the cluster's own server address and certificate and sets `proxy-url: socks5://127.0.0.1:<port>`; the proxy is `ssh -D` to the environment's control plane.

No GPU: `uv run hamidemo env setup mock` creates a local kind cluster with two fake A100-40GB.

## Environments

- **a100**: Kubernetes 1.36, `a100-1` (control plane, MIG enabled) and `a100-2` (Lupine remote GPU server), 2 x A100-SXM4-40GB each.
- **amd**: kind on an AMD Instinct MI300X host, HAMi 2.11.0 with amd-device-plugin 0.0.4.
- **mock**: local kind with the HAMi mock device plugin.

Known problems and their fixes (ARP on the AMD host, MIG needing a reboot, Volcano removal, kind egress): [environments/CAVEATS.md](environments/CAVEATS.md).

## Layout

- `src/hamidemo/`: the CLI (`env`, `run`, `logs`, `list`, `utilization-chart`), inference-perf Job builder and report charts
- `config.toml.example`: site topology template (copy to `config.toml`)
- `environments/helmfile.yaml.gotmpl`: one Helmfile for all environments, `helmfile -e <name>`; HAMi version declared once
- `environments/common.yaml`, `environments/<name>/values.yaml.gotmpl`: shared and per-environment values
- `environments/<name>/environment.yaml`: reusable recipe (kind cluster, sysctls, conflicts, node preparation)
- `demos/<name>/demo.yaml` plus manifests; `demos/bench/*.yaml`: shared load profiles

Demo details: [demos/README.md](demos/README.md).

## Legacy

`Makefile`, `scripts/`, `helmfile.d/`, `charts/`, `values/` and `terraform/` are the original GKE demo (`make init`, `make test1`, `make test2`). They still work but are not used by `hamidemo` and will be migrated or removed. Terraform is optional and not part of the default path.
