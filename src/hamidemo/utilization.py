"""GPU allocation view built from HAMi node and pod annotations."""

import json
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns

from .kube import kjson

NODE_REGISTER = "hami.io/node-nvidia-register"
ALLOCATED = ("hami.io/vgpu-devices-allocated", "hami.io/remote-gpu-devices-allocated")
MIG_ALLOCATIONS = "hami.io/vgpu-mig-allocations"


@dataclass
class Slice:
    pod: str
    mem: int  # MiB
    cores: int
    kind: str  # hami-core, mig:<profile>, remote


@dataclass
class Gpu:
    node: str
    uuid: str
    type: str
    mem: int
    mode: str
    slices: list[Slice] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.node}\n{self.type.replace('NVIDIA-', '')} {self.uuid[4:12]}\n{self.mode}"


def decode(value: str):
    """HAMi encoding: containers split by ';', devices by ':', fields uuid,type,mem,cores[,slots]."""
    for ctr in filter(None, value.split(";")):
        for dev in filter(None, ctr.split(":")):
            uuid, _, mem, cores, *_ = dev.split(",")
            yield uuid, int(mem), int(cores)


def collect() -> list[Gpu]:
    gpus: dict[str, Gpu] = {}
    for n in kjson("nodes")["items"]:
        reg = n["metadata"].get("annotations", {}).get(NODE_REGISTER)
        for d in json.loads(reg) if reg else []:
            gpus[d["id"]] = Gpu(n["metadata"]["name"], d["id"], d.get("type", ""), d.get("devmem", 0), d.get("mode") or "hami-core")
    for p in kjson("pods", "-A", "--field-selector=status.phase=Running")["items"]:
        ann, name = p["metadata"].get("annotations", {}), p["metadata"]["name"]
        mig = {a["gpuUUID"]: a["profile"] for a in json.loads(ann.get(MIG_ALLOCATIONS, "[]"))}
        for key in ALLOCATED:
            for uuid, mem, cores in decode(ann.get(key, "")):
                if gpu := gpus.get(uuid):
                    kind = "remote" if "remote" in key else f"mig {mig[uuid]}" if uuid in mig else "hami-core"
                    gpu.slices.append(Slice(name, mem, cores, kind))
    return sorted(gpus.values(), key=lambda g: (g.node, g.uuid))


def draw(gpus: list[Gpu], out: Path, title: str) -> Path:
    sns.set_theme(style="whitegrid", context="talk")
    pods = sorted({s.pod for g in gpus for s in g.slices})
    colors = dict(zip(pods, sns.color_palette("tab10", max(len(pods), 1))))
    fig, ax = plt.subplots(figsize=(4 + 2.2 * len(gpus), 7))
    for i, g in enumerate(gpus):
        used = 0
        for s in g.slices:
            ax.bar(i, s.mem / 1024, bottom=used / 1024, color=colors[s.pod], edgecolor="white", label=s.pod)
            ax.text(i, (used + s.mem / 2) / 1024, f"{s.kind}\n{s.mem / 1024:.1f}G", ha="center", va="center", fontsize=10, color="white")
            used += s.mem
        ax.bar(i, max(g.mem - used, 0) / 1024, bottom=used / 1024, color="#e8e8e8", edgecolor="white")
        ax.text(i, g.mem / 1024, f"{used / max(g.mem, 1):.0%}", ha="center", va="bottom", fontsize=12)
    ax.set_xticks(range(len(gpus)), [g.label for g in gpus], fontsize=10)
    ax.set_ylabel("GPU memory allocated (GiB)")
    ax.set_title(title)
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    if uniq:
        ax.legend(uniq.values(), uniq.keys(), loc="upper left", bbox_to_anchor=(1, 1), fontsize=10)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out
