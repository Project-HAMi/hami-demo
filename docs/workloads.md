---
title: Workloads
---

## GPU resource model

HAMi slices physical GPUs into virtual devices. Each workload requests:

```yaml
resources:
  limits:
    nvidia.com/gpu: 1           # 1 vGPU slot
    nvidia.com/gpumem: 25600    # VRAM in MB
    nvidia.com/gpucores: 30     # compute percentage (0-100)
```

Scheduling policy is set via annotations:

```yaml
metadata:
  annotations:
    hami.io/node-scheduler-policy: binpack  # pack onto fewest nodes
    hami.io/gpu-scheduler-policy: binpack   # pack onto fewest GPUs
```

`binpack` maximizes GPU utilization. `spread` distributes for throughput. Without annotations, HAMi defaults to spread.

## LLM inference (vLLM)

### Qwen3-1.7B

```yaml
# charts/qwen/qwen.yaml
resources:
  limits:
    nvidia.com/gpu: 1
    nvidia.com/gpumem: 25600   # 25 GB
    nvidia.com/gpucores: 0
```

Exposes OpenAI-compatible API on port 8000. Good baseline for testing.

### Qwen3-8B (long running)

```yaml
# charts/qwen_longrunning/qwen.yaml
resources:
  limits:
    nvidia.com/gpu: 1
    nvidia.com/gpumem: 25600
    nvidia.com/gpucores: 0
nodeSelector:
  cloud.google.com/gke-nodepool: default-pool  # dedicated node
```

Pinned to a specific node to avoid rescheduling. Exposes API on port 8020.

### Qwen3-4B MIG

```yaml
# charts/mig/qwen3-4b-mig.yaml
resources:
  limits:
    nvidia.com/gpu: 1
    nvidia.com/gpumem: 10240   # 10 GB
    nvidia.com/vgpu-mode: "mig"
```

Runs in MIG mode for hardware-level GPU partitioning.

### vLLM production stack

```yaml
# helmfile.d/06-vllm.yaml deploys via vLLM Helm chart
```

Two models:
- **BAAI/bge-m3** (14 GB): text embeddings
- **BAAI/bge-reranker-v2-m3** (8 GB): re-ranking

Both are served as OpenAI-compatible endpoints by a single vLLM instance.

### Spread workload

```yaml
# charts/spread/spread.yaml
spec:
  replicas: 4
  template:
    spec:
      containers:
      - resources:
          limits:
            nvidia.com/gpu: 1
            nvidia.com/gpumem: 15360   # 15 GB each × 4 = 60 GB total
```

Four replicas of Qwen3-4B. With binpack they pack onto minimum GPUs.

## Computer vision (YOLOv8)

```yaml
# charts/yolo8/yolov8n.yaml
spec:
  replicas: 5
  template:
    spec:
      containers:
      - image: ultralytics/ultralytics:latest
        resources:
          limits:
            nvidia.com/gpu: 1
            nvidia.com/gpumem: 1024    # 1 GB; yolov8n only needs ~600 MB
```

Uses `yolov8n.pt` (6.2 MB model, ~80 FPS on A100). Light enough that 5 replicas × 1 GB = 5 GB on one A100.

### Model size reference

| Model | Size | GPU memory | FPS (A100) |
|-------|------|-----------|------------|
| yolov8n | 6 MB | ~1 GB | 80+ |
| yolov8s | 23 MB | ~2-3 GB | 45 |
| yolov8m | 52 MB | ~6-8 GB | 25 |
| yolov8l | 87 MB | ~10-12 GB | 18 |
| yolov8x | 140 MB | ~15-18 GB | 12 |

## Testing workloads

```bash
# vLLM OpenAI-compatible API test
python3 test_vllm.py

# YOLOv8 inference test in-cluster
kubectl exec -it <yolov8-pod> -- python -c "
from ultralytics import YOLO
import numpy as np
model = YOLO('yolov8n.pt')
img = np.random.randint(0, 255, (640, 480, 3), dtype=np.uint8)
results = model(img)
print(f'Detections: {len(results)}')
"

# GPU memory check
kubectl exec -it <pod> -- nvidia-smi
```

## GPU visualization

```bash
python3 scripts/gpu_visualization.py
# Outputs:
#   output/interactive_dashboard.html  - Plotly interactive dashboard
#   output/visualization_report.txt    - Text summary
```

The dashboard shows VRAM utilization per node and per workload type.
