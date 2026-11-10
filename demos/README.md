# Demos

`hamidemo` deploys environments, runs demos and draws per-GPU allocation from HAMi annotations.

- `environments/helmfile.yaml.gotmpl`: one Helmfile for all environments (`helmfile -e <name>`). HAMi 2.11.0 is declared once; `common.yaml` holds shared values, `<name>/values.yaml.gotmpl` only what differs.
- `environments/<name>/environment.yaml`: SSH access, kind cluster, node prep.
- `demos/<name>/demo.yaml` plus manifests.
- `demos/bench/*.yaml`: inference-perf load profiles shared by the demos.

Every run writes `output/<demo>/<timestamp>/` with `run.log`, raw inference-perf reports, a TTFT / TPOT / E2E latency chart per QPS stage and allocation PNGs (before, per phase, after).

Known problems per environment: [environments/CAVEATS.md](../environments/CAVEATS.md).

Needs `uv`, `kubectl`, `helm`, `helmfile`, `ssh` and a `config.toml` (copy [config.toml.example](../config.toml.example)).

```bash
uv run hamidemo env list
uv run hamidemo env connect a100                       # kubeconfig + SSH tunnel; prints the export
export KUBECONFIG=$PWD/environments/a100/kubeconfig
uv run hamidemo env setup a100 --remove-conflicts      # once: replaces the Volcano vGPU plugin with HAMi 2.11.0; enabling MIG needs a reboot of a100-1 (see CAVEATS)
uv run hamidemo list
uv run hamidemo run vllm-mig
uv run hamidemo run remote-cuda                        # CLIENT_NODE=a100-2 to swap roles
uv run hamidemo run epp-burst
uv run hamidemo utilization-chart                      # current allocation, any HAMi cluster
```

`--keep` leaves the workloads running.

## Environments

- **mock**: local kind, mock device plugin with two fake A100-40GB. No GPU needed.
- **amd**: kind on the MI300X host with `/dev/kfd` and `/dev/dri` mounted, HAMi plus amd-device-plugin 0.0.4. Run `hamidemo env setup amd` on that host, or `env connect amd` from your machine afterwards.
- **a100**: existing Kubernetes 1.36 cluster, `a100-1` (poff1) and `a100-2` (poff2), 2 x A100-SXM4-40GB each. `a100-1` runs in Flexible MIG mode, `a100-2` as the Lupine remote GPU server.

## pitch

The 20 minute walkthrough: `vllm-mig` and the CUDA part of `remote-cuda` in one run, no benchmark.

```text
 a100-1 (MIG_NODE, CLIENT_NODE)                      a100-2 (REMOTE_SERVER_NODE, no MIG)
 +-----------------------------------------+         +---------------------------+
 | GPU 0: 2g.10gb vllm-mig-small   (1st)   |         | GPU 0, GPU 1 (whole)      |
 |        3g.20gb vllm-mig-large   (2nd)   |         | served by Lupine          |
 | GPU 1: free                             |         |                           |
 | remote-cuda pod, no local GPU  ---------+-------> | runs its CUDA kernels     |
 +-----------------------------------------+         +---------------------------+
```

The remote-cuda pod prints the node it runs on (`a100-1`, `NVIDIA_VISIBLE_DEVICES=void`), then `nvidia-smi -L` lists two A100s "via lupine 10.178.0.50" (a100-2). Measured: 414 s for the whole run with images cached, most of it vLLM loading the two models; the cold start fits a 20 minute slot, so it can run live; `--keep` leaves it up for questions. HAMi keeps released MIG instances for reuse, so to show them being created live, clear them on a100-1 first (`sudo nvidia-smi mig -dci && sudo nvidia-smi mig -dgi`, then restart the a100-1 `hami-device-plugin` pod).

## vllm-mig

vLLM Qwen3-1.7B with `nvidia.com/gpumem: 9000`, then Qwen3-4B with `20000`, both annotated `nvidia.com/vgpu-mode: mig` and binpack, pinned to MIG_NODE. HAMi picks the smallest allowlisted profile that fits (2g.10gb and 3g.20gb on an A100-40GB; 2g.10gb registers as 9984 MiB, so a 10000 request would get 3g.20gb) and creates it when the pod is scheduled. The small server is up before the large one is applied, and binpack puts both on the same A100. The run logs the `hami.io/vgpu-mig-allocations` reservation, `nvidia-smi -L` per pod and a completion per server. A released instance is kept and reused by the next pod that fits it.

```text
 a100-1, one A100-SXM4-40GB in MIG mode (7 compute slices, 40 GB)

 pod request (in order)        HAMi creates      GPU layout
 +-----------------------+
 | 1. vllm-mig-small     | --> 2g.10gb -----+
 | gpumem 9000, mig      |                  |    +---------+-----------+---------+
 +-----------------------+                  +--> | 2g.10gb |  3g.20gb  | 2 free  |
 | 2. vllm-mig-large     | --> 3g.20gb -----+    +---------+-----------+---------+
 | gpumem 20000, mig     |
 +-----------------------+
```

## vllm-amd-share

Two upstream vLLM ROCm servers (`vllm/vllm-openai-rocm:v0.32.0`, no custom image) on the one MI300X in the `amd` environment: Qwen3-1.7B with `amd.com/gpumem: 24576` and `amd.com/gpucores: 25`, Qwen3-8B with `65536` and `50`. The run logs each pod's HIP/ROCR environment, a completion per server and the same inference-perf ramp. llama.cpp is only used by the workshop's laptop lab (Radeon 780M); the Instinct path uses standard vLLM images.

```text
 kind node hami-amd-control-plane, one AMD Instinct MI300X VF (304 CUs, 192 GB)

 +---------------------------+      +-------------------------------------------+
 | vllm-amd-small            |      | compute units                             |
 | Qwen3-1.7B                | ---> | 0 ......... 75 | 76 ............ 227 | free |
 | gpumem 24 GiB, cores 25%  |      |   25% (small)  |    50% (large)      |      |
 +---------------------------+      +-------------------------------------------+
 | vllm-amd-large            | ---> | memory                                    |
 | Qwen3-8B                  |      | 24 GiB | 64 GiB |          free           |
 | gpumem 64 GiB, cores 50%  |      +-------------------------------------------+
 +---------------------------+
   HAMi sets HSA_CU_MASK per pod; amd-device-plugin enforces the memory cap
```

CU ranges are from the 2026-11-10 run (`HSA_CU_MASK=0:0-75` and `0:76-227`).

## remote-cuda

On `CLIENT_NODE`, with the GPUs on the Lupine server: a CUDA kernel checking `35 + 7 = 42` on both remote GPUs, then vLLM Qwen3-8B with tensor parallel 2 over them, a completion and the same inference-perf ramp. A workload reserves the whole server, so the phases run in sequence.

```text
 CLIENT_NODE (default a100-1)              Lupine server (default a100-2)
 +-----------------------------+           +-----------------------------+
 | pod: CUDA kernel or vLLM    |  CUDA     | hami-lupine-server          |
 | TP=2, no local GPU used     | --------> |  +-------+   +-------+      |
 | remote-gpu-devices-allocated|  calls    |  | A100  |   | A100  |      |
 +-----------------------------+  over the |  | 40 GB |   | 40 GB |      |
                                  network  |  +-------+   +-------+      |
                                           +-----------------------------+
```

## epp-burst

Uses the llm-d quickstart release already in `llm-d-quickstart` (EPP with `prefix-cache-affinity-filter` and `token-load-scorer`). Scales the Qwen3-8B TP=2 vLLM deployment to 2 replicas, then runs the same shared-prefix burst twice: through `quickstart-epp` and straight to the `decode-random` Service. The load is 24 system prompts of 6000 tokens, each reused by 30 users, at 1, 6 and 10 QPS. The run reports TTFT, TPOT and E2E latency (p50, p90) and output tokens/s per stage for both routes, and scales vLLM back to 0.

```text
 inference-perf: 24 prompts x 6000 tokens, 30 users each, 1 / 6 / 10 QPS

 run 1: cache-aware                         run 2: baseline
 +----------------+                         +----------------+
 | inference-perf |                         | inference-perf |
 +-------+--------+                         +-------+--------+
         |                                          |
 +-------v-------------------------+        +-------v--------+
 | quickstart-epp                  |        | decode-random  |
 | prefix-cache-affinity-filter    |        | Service        |
 | token-load-scorer               |        | (round robin)  |
 +---+-------------------------+---+        +---+--------+---+
     | same prefix,            |                |        |
     | same replica            |                |        |
 +---v-------+       +---------v-+        +-----v-----+ +v----------+
 | vLLM #1   |       | vLLM #2   |        | vLLM #1   | | vLLM #2   |
 | TP=2      |       | TP=2      |        | TP=2      | | TP=2      |
 +-----------+       +-----------+        +-----------+ +-----------+

 compare TTFT, TPOT, E2E latency (p50, p90) and output tokens/s per stage
```

Sources: [Dynamic MIG](https://project-hami.io/docs/userguide/nvidia-device/dynamic-mig-support), [Remote GPU](https://project-hami.io/docs/userguide/nvidia-device/remote-gpu), HAMi `examples/remotegpu` at v2.11.0, [inference-perf reports](https://github.com/kubernetes-sigs/inference-perf/blob/main/docs/reports.md).
