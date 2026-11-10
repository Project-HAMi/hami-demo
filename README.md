# HAMi KubeCon Demos

Reproducible HAMi demos on three environments, driven by one Python CLI, `hamidemo`:

- **vllm-mig**: two vLLM servers on dynamically created MIG instances (HAMi Flexible MIG)
- **remote-cuda**: CUDA and vLLM on GPUs from another node over HAMi Remote GPU (Lupine)
- **epp-burst**: llm-d EPP cache-aware routing against random routing under a shared-prefix burst, measured as TTFT

Every demo uses the same benchmark path ([inference-perf](https://github.com/kubernetes-sigs/inference-perf)) and writes its raw reports, a TTFT / TPOT / latency chart per QPS stage and per-GPU allocation charts to `output/<demo>/<timestamp>/`.

## Quick start

Needs `uv`, `kubectl`, `helm`, `helmfile`, `ssh` and the shared SSH key `~/.ssh/id_kubecon`.

```bash
uv run hamidemo env list
uv run hamidemo env connect a100                    # kubeconfig + SSH tunnel; prints the export
export KUBECONFIG=$PWD/environments/a100/kubeconfig
uv run hamidemo env setup a100 --remove-conflicts   # once: installs HAMi 2.11.0, replaces the Volcano vGPU plugin
uv run hamidemo list
uv run hamidemo run vllm-mig
uv run hamidemo run remote-cuda
uv run hamidemo run epp-burst
uv run hamidemo utilization-chart                   # current allocation on any HAMi cluster
```

No GPU: `uv run hamidemo env setup mock` creates a local kind cluster with two fake A100-40GB.

## Environments

- **a100**: Kubernetes 1.36, `a100-1` (control plane, MIG enabled) and `a100-2` (Lupine remote GPU server), 2 x A100-SXM4-40GB each.
- **amd**: kind on an AMD Instinct MI300X host, HAMi 2.11.0 with amd-device-plugin 0.0.4.
- **mock**: local kind with the HAMi mock device plugin.

Known problems and their fixes (ARP on the AMD host, MIG needing a reboot, Volcano removal, kind egress): [environments/CAVEATS.md](environments/CAVEATS.md).

## Layout

- `src/hamidemo/`: the CLI (`env`, `run`, `list`, `utilization-chart`), inference-perf Job builder and report charts
- `environments/helmfile.yaml.gotmpl`: one Helmfile for all environments, `helmfile -e <name>`; HAMi version declared once
- `environments/common.yaml`, `environments/<name>/values.yaml.gotmpl`: shared and per-environment values
- `environments/<name>/environment.yaml`: SSH access, kind cluster, node preparation
- `demos/<name>/demo.yaml` plus manifests; `demos/bench/*.yaml`: shared load profiles

Demo details: [demos/README.md](demos/README.md).

## Legacy

`Makefile`, `scripts/`, `helmfile.d/`, `charts/`, `values/` and `terraform/` are the original GKE demo (`make init`, `make test1`, `make test2`). They still work but are not used by `hamidemo` and will be migrated or removed. Terraform is optional and not part of the default path.
