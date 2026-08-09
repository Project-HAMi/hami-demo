---
title: Deployment
---

## Prerequisites

- GCP project with Compute Engine, GKE, and IAM APIs enabled
- `gcloud` authenticated, `kubectl`, `helm` 3+, `helmfile`
- Terraform 1.x
- Python 3.8+ with `pip install -r requirements.txt`

## 1. Provision the cluster

```bash
cd terraform
terraform init
terraform apply -var="project_id=dynamia-ai"
```

This creates:
- GKE cluster in `asia-northeast3-b`
- `a2-highgpu-2g` node pool (2× A100 80GB per node)
- Ubuntu containerd image (COS won't work: read-only rootfs blocks NVIDIA drivers)
- Kubernetes admin service account with full cluster-admin RBAC

The terraform output includes a `kubeconfig` command. Run it to configure `kubectl`.

## 2. Install NVIDIA drivers

GKE nodes don't ship with NVIDIA drivers. Use the startup script or the preloaded daemonset:

```bash
# Via helmfile (recommended):
helmfile sync
```

This installs the NVIDIA driver daemonset and GPU Operator via the HAMi core chart. Nodes take ~5 minutes to register GPU capacity after driver install.

Verify:

```bash
kubectl get nodes -o json | jq '.items[0].status.capacity["nvidia.com/gpu"]'
```

## 3. Deploy HAMi and workloads

```bash
make init
```

What happens under the hood:
1. HAMi Helm repo added
2. NVIDIA GPU Operator installed (CDI mode, device plugin disabled; HAMi takes over)
3. HAMi core deployed (scheduler extender + device plugin + MutatingWebhook)
4. All workloads deployed: Qwen vLLM variants, YOLOv8n, vLLM production stack (BGE embedding + reranker)

## 4. Verify the deployment

```bash
make status
kubectl get pods -l app=qwen7b
kubectl get pods -l app=yolov8n
```

Check GPU allocation:

```bash
kubectl describe pod <pod-name> | grep -A5 "Limits:"
# Should show nvidia.com/gpu: 1, nvidia.com/gpumem: <MB>, nvidia.com/gpucores: <percent>
```

Run the test harness:

```bash
make test1    # Qwen inference test
./scripts/control.sh --test-qwen7b --port-forward
python3 test_vllm.py
```

## Makefile reference

| Command | What it does |
|---------|-------------|
| `make init` | Deploy HAMi core + all workloads |
| `make test1` | Deploy Qwen + run API test + viz |
| `make test2` | Deploy MIG pod + run test |
| `make clean` | Destroy workloads (Qwen + MIG) |
| `make status` | Cluster and GPU workload status |
| `make check-deps` | Verify prerequisites |
| `make help` | All available commands |

## Helmfile reference

Each file in `helmfile.d/` deploys a subsystem:

| File | Purpose |
|------|---------|
| `00-nvidia.yaml` | NVIDIA GPU Operator + driver daemonset |
| `01-hami-core.yaml` | HAMi scheduler extender + device plugin |
| `02-workload.yaml` | Long-running Qwen workload |
| `03-cv-deployment.yaml` | YOLOv8n × replicas |
| `04-qwen.yaml` | Qwen3-1.7B vLLM |
| `05-mig.yaml` | Qwen3-4B MIG test |
| `06-vllm.yaml` | vLLM prod stack (BGE embedding + reranker) |

Deploy subsets:

```bash
helmfile -f helmfile.d/03-cv-deployment.yaml apply
helmfile -f helmfile.d/04-qwen.yaml apply
```
