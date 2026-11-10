# Demos

`hamidemo` deploys environments, runs demos and draws per-GPU allocation from HAMi annotations.

- `environments/helmfile.yaml.gotmpl`: one Helmfile for all environments (`helmfile -e <name>`). HAMi 2.11.0 is declared once; `common.yaml` holds shared values, `<name>/values.yaml.gotmpl` only what differs.
- `environments/<name>/environment.yaml`: SSH access, kind cluster, node prep.
- `demos/<name>/demo.yaml` plus manifests.

Every run writes `output/<demo>/<timestamp>/` with `run.log`, raw benchmarks and allocation PNGs (before, per phase, after).

Needs `uv`, `kubectl`, `helm`, `helmfile`, `ssh` and `~/.ssh/id_kubecon`.

```bash
uv run hamidemo env list
uv run hamidemo env connect a100                       # kubeconfig + SSH tunnel; prints the export
export KUBECONFIG=$PWD/environments/a100/kubeconfig
uv run hamidemo env setup a100 --remove-conflicts      # once: replaces the Volcano vGPU plugin with HAMi 2.11.0
uv run hamidemo list
uv run hamidemo run vllm-mig
uv run hamidemo run remote-cuda                        # CLIENT_NODE=a100-2 to swap roles
uv run hamidemo utilization-chart                      # current allocation, any HAMi cluster
```

`--keep` leaves the workloads running.

## Environments

- **mock**: local kind, mock device plugin with two fake A100-40GB. No GPU needed.
- **amd**: kind on the MI300X host with `/dev/kfd` and `/dev/dri` mounted, HAMi plus amd-device-plugin 0.0.4. Run `hamidemo env setup amd` on that host, or `env connect amd` from your machine afterwards.
- **a100**: existing Kubernetes 1.36 cluster, `a100-1` (poff1) and `a100-2` (poff2), 2 x A100-SXM4-40GB each. `a100-1` runs in Flexible MIG mode, `a100-2` as the Lupine remote GPU server.

## vllm-mig

vLLM Qwen3-1.7B with `nvidia.com/gpumem: 10000` and Qwen3-4B with `20000`, both annotated `nvidia.com/vgpu-mode: mig`. HAMi picks the smallest allowlisted profile that fits (2g.10gb and 3g.20gb on an A100-40GB) and creates it when the pod starts. The run logs the `hami.io/vgpu-mig-allocations` reservation, `nvidia-smi -L` per pod, a completion per server and a concurrent `vllm bench serve`, then deletes the pods so HAMi reclaims the instances.

## remote-cuda

On `CLIENT_NODE`, with the GPUs on the Lupine server: a CUDA kernel checking `35 + 7 = 42` on both remote GPUs, then vLLM Qwen3-8B with tensor parallel 2 over them, a completion and a benchmark. A workload reserves the whole server, so the phases run in sequence.

Sources: [Dynamic MIG](https://project-hami.io/docs/userguide/nvidia-device/dynamic-mig-support), [Remote GPU](https://project-hami.io/docs/userguide/nvidia-device/remote-gpu), HAMi `examples/remotegpu` at v2.11.0.
