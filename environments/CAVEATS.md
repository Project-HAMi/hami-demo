# Environment caveats

Problems found while bringing up the demo environments, with cause, fix and current state.

## amd: pods get `no route to host` inside kind

**Symptom.** In the `hami-amd` kind cluster on the MI300X host, every pod connection fails with `dial tcp 10.96.0.1:443: connect: no route to host`. HAMi's `hami-admission-create` job crash-loops, so `helm install` never finishes. `local-path-provisioner` crash-loops for the same reason. The kind node itself can reach the API server.

**Cause.** The host ships `/etc/sysctl.d/99-prevent-arp-flux.conf`:

```
net.ipv4.conf.default.arp_announce=1
net.ipv4.conf.all.arp_announce=1
net.ipv4.conf.default.arp_ignore=2
net.ipv4.conf.all.arp_ignore=2
```

The kind node's network namespace inherits `default.arp_ignore=2`, so every pod veth kindnet creates gets it too. With `arp_ignore=2` the node only answers ARP for addresses on the same subnet as the receiving interface. The pod gateway `10.244.0.1` sits on `/32` veths, so the node never answers the pod's ARP request for its gateway (`ip neigh` in the pod shows `10.244.0.1 dev eth0 FAILED`). Nothing leaves the pod. The host firewall (ufw, `FORWARD DROP`) is not involved.

**Fix.** Set `arp_ignore=0` inside the kind node only, for new interfaces and for existing pod veths:

```bash
docker exec hami-amd-control-plane sh -c '
  echo 0 > /proc/sys/net/ipv4/conf/default/arp_ignore
  for f in /proc/sys/net/ipv4/conf/veth*/arp_ignore; do echo 0 > "$f"; done'
```

Then delete pods that started before the fix so they get fresh veths. The host's own sysctls are left alone.

**State.**

- Applied by hand on the box.
- After the fix a test pod got a TCP connection and a TLS reply from the API server where it had `no route to host` before.
- busybox `wget` then rejected the TLS handshake (alert 47). That is a busybox TLS limitation, not this bug. A clean HTTP response from a curl pod is not confirmed yet.
- The setting lives in the kind node container, so it is lost when the node container restarts or the cluster is recreated. `hamidemo env setup amd` reapplies it on every run (`kind.node_sysctls` in `amd/environment.yaml`) and deletes pods that are not Running so they get fresh veths.
- Any host that sets `arp_ignore=2` by default (common on cloud images that "prevent ARP flux") will hit this with kind.

## amd: the workshop lab targets a laptop iGPU

`docs/labs/local-amd-gpu.md` in hami-workshop is written for a Radeon iGPU laptop. On the MI300X host (one VF, compute partition fixed at SPX, memory NPS1) it needs the ARP fix above, and the device plugin reports one card, not partitions.

## a100: Volcano vGPU plugin and HAMi cannot coexist

Both register GPU resources on the same nodes. `hamidemo env setup a100 --remove-conflicts` uninstalls the `volcano-vgpu-device-plugin` Helm release before installing HAMi. Volcano's scheduler, admission and controllers stay installed. Workloads that request `volcano.sh/vgpu-*` (the old `vgpu-test/vllm-qwen3-8b` pod) can no longer schedule.

## a100: enabling MIG needs a reboot on these VMs

`nvidia-smi -mig 1` leaves the mode as `Disabled` (current) / `Enabled` (pending). `nvidia-smi -r` returns `Not Supported` for both GPUs on these GCE VMs, so the only way to apply it is a reboot of the node. a100-1 is the control plane, so the API server is down while it reboots.

## a100: SSH key lost after rebooting poff1

After the reboot, poff1 (`34.64.252.7`, a100-1) refuses `~/.ssh/id_kubecon` (`Permission denied (publickey)`). poff2 still accepts it. The likely cause is the GCE guest agent rewriting `authorized_keys` from instance or project metadata at boot, which drops keys added by hand. Fix: add the `id_kubecon` public key to the instance or project SSH metadata in the GCP console (not in `authorized_keys`), so it survives reboots. Until then the cluster cannot be reached through `hamidemo env connect a100`, and the API server state after the reboot is unverified.

## local kind: no outbound DNS from pods

In the local `hami-lab` kind cluster, pods cannot resolve external hosts (`curl: (6) Could not resolve host: quay.io`). inference-perf Jobs fail there when loading the tokenizer from Hugging Face. The benchmark path was verified by running the same inference-perf image under plain Docker with its mock server instead.
