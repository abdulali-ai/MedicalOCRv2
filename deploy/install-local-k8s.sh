#!/bin/bash
# Install k3s, build the local images, and apply the OCR manifests.
# Run as root. Progress is written to /tmp/ocr-k8s-install.log.
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export DEBIAN_FRONTEND=noninteractive
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

LOG=/tmp/ocr-k8s-install.log
exec > >(tee -a "$LOG") 2>&1
echo "===== install started $(date -Is) ====="

REPO=/home/utec/ocr
cd "$REPO"

apt-get update
apt-get install -y docker.io curl ca-certificates gnupg
systemctl enable --now docker

if ! dpkg -s nvidia-container-toolkit >/dev/null 2>&1; then
  if ! apt-get install -y nvidia-container-toolkit; then
    install -d /usr/share/keyrings
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
      | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update
    apt-get install -y nvidia-container-toolkit
  fi
fi
nvidia-ctk runtime configure --runtime=docker || true
systemctl restart docker

if ! command -v k3s >/dev/null 2>&1; then
  curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644
fi
systemctl enable --now k3s

# Register the NVIDIA runtime with k3s containerd without making it the default.
python3 - <<'PY'
from pathlib import Path
config = Path("/var/lib/rancher/k3s/agent/etc/containerd/config.toml")
text = config.read_text() if config.exists() else ""
if "runtimes.nvidia" in text or "runtimes.'nvidia'" in text:
    print("nvidia runtime already present")
    raise SystemExit(0)
# containerd 2 (k3s 1.32+) uses the cri.v1 plugin id. Older k3s uses grpc.v1.cri.
plugin = "io.containerd.cri.v1.runtime" if "io.containerd.cri.v1.runtime" in text or not text else "io.containerd.grpc.v1.cri"
if "io.containerd.grpc.v1.cri" in text and "io.containerd.cri.v1.runtime" not in text:
    plugin = "io.containerd.grpc.v1.cri"
snippet = f"""
[plugins."{plugin}".containerd.runtimes.nvidia]
  privileged_without_host_devices = false
  runtime_type = "io.containerd.runc.v2"
  [plugins."{plugin}".containerd.runtimes.nvidia.options]
    BinaryName = "/usr/bin/nvidia-container-runtime"
    SystemdCgroup = true
"""
tmpl = Path("/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl")
base = text if text.strip() else ""
tmpl.write_text(base + "\n" + snippet)
print(f"wrote {tmpl} using plugin {plugin}")
PY
systemctl restart k3s

for i in $(seq 1 60); do
  if kubectl get nodes 2>/dev/null | grep -q ' Ready'; then
    break
  fi
  sleep 2
done
kubectl get nodes

# Device plugin advertises nvidia.com/gpu to the scheduler.
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.17.1/deployments/static/nvidia-device-plugin.yml \
  || kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.17.0/deployments/static/nvidia-device-plugin.yml

echo "===== building images $(date -Is) ====="
docker build -f "$REPO/deploy/docker/Dockerfile.api" -t ocr-api:local "$REPO"
docker build -f "$REPO/deploy/docker/Dockerfile.vision" -t ocr-vision:local "$REPO"
docker save ocr-api:local | k3s ctr images import -
docker save ocr-vision:local | k3s ctr images import -
k3s ctr images ls | grep -E 'ocr-api|ocr-vision' || true

install -d -m 0777 /var/lib/ocr-storage /var/lib/ocr-hf
install -d -m 0755 /home/utec/.kube
cp /etc/rancher/k3s/k3s.yaml /home/utec/.kube/config
chown utec:utec /home/utec/.kube/config
chmod 600 /home/utec/.kube/config
usermod -aG docker utec || true

echo "===== applying manifests $(date -Is) ====="
kubectl apply -f "$REPO/deploy/k8s/namespace.yaml"
kubectl apply -f "$REPO/deploy/k8s/configmap.yaml"
kubectl apply -f "$REPO/deploy/k8s/local-storage.yaml"
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: redis-data
  namespace: ocr
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi
EOF
kubectl apply -f "$REPO/deploy/k8s/redis.yaml"
kubectl apply -f "$REPO/deploy/k8s/api.yaml"
kubectl apply -f "$REPO/deploy/k8s/workers.yaml"
# vLLM runs on the host via PM2 (see deploy/vllm/ecosystem.config.cjs),
# not as a GPU container — that was OOMing / crashing the machine.
kubectl apply -f "$REPO/deploy/k8s/vllm-host.yaml"

echo "===== waiting for workloads $(date -Is) ====="
kubectl -n ocr rollout status deployment/redis --timeout=180s || true
kubectl -n ocr rollout status deployment/api --timeout=180s || true
kubectl -n ocr rollout status deployment/pdf-worker --timeout=180s || true
kubectl -n ocr rollout status deployment/vision-worker --timeout=240s || true
kubectl -n ocr rollout status deployment/extract-dispatcher --timeout=180s || true
kubectl -n ocr get pods -o wide || true
kubectl -n kube-system get pods -o wide | grep -E 'nvidia|NAME' || true
echo "===== install finished $(date -Is) ====="
echo "Start host vLLM with: pm2 start $REPO/deploy/vllm/ecosystem.config.cjs"
