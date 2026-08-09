---
title: Overview
---

Reference deployment for GPU sharing on Kubernetes using [HAMi](https://github.com/Project-HAMi/HAMi) (CNCF Sandbox). Provisions an A100 GKE cluster, deploys LLM inference (Qwen/vLLM) and computer vision (YOLOv8) workloads with fractional GPU allocation, and demonstrates HAMi's vGPU scheduling with binpack and spread policies.

## What this demonstrates

- **HAMi vGPU scheduling:** multiple workloads sharing A100 GPUs via `nvidia.com/gpumem` and `nvidia.com/gpucores` resource slicing, with binpack and spread policies
- **Multi-model inference:** Qwen3 variants (1.7B/4B/8B) via vLLM, BGE embeddings/reranking, YOLOv8n object detection, all running concurrently on the same GPU nodes
- **Zero-to-running IaC:** Terraform provisions GKE, Helmfile deploys the full stack, Makefile drives the workflow
- **GPU resource slicing:** workloads request fractional GPUs by VRAM (`nvidia.com/gpumem`) and compute percentage (`nvidia.com/gpucores`) instead of full devices

## Quick start

```bash
# Provision GKE cluster
cd terraform && terraform init && terraform apply

# Deploy HAMi core and all workloads
make init

# Run inference test
make test1

# See what's running
make status
```

## Architecture

```
GKE (a2-highgpu-2g, 2x A100 80GB)
├── NVIDIA GPU Operator (driver + toolkit + CDI)
├── HAMi Core (scheduler extender + device plugin)
│   ├── MutatingWebhook → rewrites schedulerName
│   ├── Scheduler extender → device-aware filter/score/bind
│   └── Device plugin → vGPU slots on physical GPUs
└── Workloads
    ├── Qwen3-8B vLLM  (25.6 GB, port 8020)
    ├── Qwen3-1.7B vLLM (25 GB, port 8000)
    ├── BGE-M3 embeddings (14 GB)
    ├── BGE Reranker v2   (8 GB)
    ├── Qwen3-4B MIG      (10 GB)
    └── YOLOv8n ×5         (1 GB each)
```

## Tech stack

| Layer | Components |
|-------|-----------|
| Cloud | GCP, GKE, A100 GPUs |
| IaC | Terraform, Helmfile, Helm |
| GPU virt | HAMi v2.9.0, NVIDIA GPU Operator v26.3.1 |
| Inference | vLLM, Ultralytics YOLOv8n, BGE models |
| Scheduling | HAMi scheduler extender, binpack/spread policies |
| Tooling | Python, Bash, Go, Plotly |

## Project layout

```
hami-demo/
├── docs/              ← you are here
├── terraform/         GKE cluster provisioning
├── helmfile.d/        HAMi + workload Helmfile configs
├── charts/            Raw Kubernetes manifests
├── scripts/           Test harness, viz, port-forward tooling
├── values/            vLLM production stack configs
└── Makefile           Entry point for all operations
```

## Requirements

- GCP project with GKE API enabled
- Terraform 1.x, Helm 3+, kubectl
- Python 3.8+ (`pip install -r requirements.txt`)
