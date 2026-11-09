#!/usr/bin/env bash
# Rebuilds the whole OpsWatch lab from nothing, from Azure Cloud Shell:
# resource group -> 4 VMs (main.bicep) -> ops-ctl admin key -> site.yml on ops-ctl (lab + OpsWatch).
# No SSH needed: the playbook runs on ops-ctl through the Azure VM agent.
#   ALLOWED_IP=203.0.113.7 PUBKEY_FILE=~/id_ed25519.pub bash lab/azure/rebuild.sh
# Optional: RG (default rg-opswatch-lab), LOCATION (eastus), VM_SIZE (Standard_F1als_v7),
#           DISK_CONTROLLER (NVMe), TZ_NAME (Pacific Standard Time), OPSWATCH_VERSION (spec-v1-implementation),
#           YES=1 to skip the delete confirmation.
set -euo pipefail
cd "$(dirname "$0")/../.."

RG=${RG:-rg-opswatch-lab}
LOCATION=${LOCATION:-eastus}
VM_SIZE=${VM_SIZE:-Standard_F1als_v7}
DISK_CONTROLLER=${DISK_CONTROLLER:-NVMe}
TZ_NAME=${TZ_NAME:-Pacific Standard Time}
OPSWATCH_VERSION=${OPSWATCH_VERSION:-spec-v1-implementation}
: "${ALLOWED_IP:?set ALLOWED_IP to your laptop's public IP (curl.exe ifconfig.me on the laptop)}"
: "${PUBKEY_FILE:?set PUBKEY_FILE to your laptop's PUBLIC key file, e.g. ~/id_ed25519.pub}"
PUBKEY=$(cat "$PUBKEY_FILE")
case "$PUBKEY" in ssh-ed25519\ *|ssh-rsa\ *) ;; *) echo "$PUBKEY_FILE is not an SSH public key" >&2; exit 1;; esac

start=$(date +%s)
step() { echo; echo "== [$(( $(date +%s) - start ))s] $*"; }

if [ "$(az group exists -n "$RG")" = "true" ]; then
  if [ "${YES:-}" != "1" ]; then
    read -r -p "Delete resource group $RG and everything in it? Type the name to confirm: " answer
    [ "$answer" = "$RG" ] || { echo "Not confirmed; nothing changed."; exit 1; }
  fi
  step "Deleting $RG"
  az group delete -n "$RG" --yes
fi

step "Creating $RG in $LOCATION"
az group create -n "$RG" -l "$LOCATION" -o none

step "Deploying 4 VMs ($VM_SIZE, $DISK_CONTROLLER)"
az deployment group create -g "$RG" -f lab/azure/main.bicep -o none \
  -p adminPublicKey="$PUBKEY" allowedSshSource="$ALLOWED_IP/32" \
     vmSize="$VM_SIZE" diskControllerType="$DISK_CONTROLLER" shutdownTimeZone="$TZ_NAME"
IP=$(az deployment group show -g "$RG" -n main --query properties.outputs.opsCtlPublicIp.value -o tsv)

step "Giving ops-ctl its own admin key"
RG="$RG" bash lab/azure/bootstrap-admin-key.sh

step "Running site.yml on ops-ctl (lab roles + OpsWatch $OPSWATCH_VERSION); takes several minutes"
out=$(az vm run-command invoke -g "$RG" -n opswatch-ops-ctl --command-id RunShellScript -o tsv \
  --query "value[0].message" --scripts "
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -q && apt-get install -y -q ansible git
sudo -iu azureuser bash -c '
  set -e
  rm -rf ~/lab-build && git clone -q -b $OPSWATCH_VERSION https://github.com/oji-infra-ops/opswatch.git ~/lab-build
  cd ~/lab-build
  ansible all -i lab/ansible/hosts.ini -m ping -o
  ANSIBLE_FORCE_COLOR=0 ansible-playbook -i lab/ansible/hosts.ini lab/ansible/site.yml -e opswatch_version=$OPSWATCH_VERSION
'")
echo "$out" | grep -E "PLAY RECAP|ok=|UNREACHABLE|FAILED|fatal" || true
if echo "$out" | grep -qE "failed=[1-9]|unreachable=[1-9]|fatal:"; then
  echo "$out" | tail -60
  echo; echo "REBUILD FAILED after $(( $(date +%s) - start ))s"; exit 1
fi

echo
echo "REBUILD OK in $(( $(date +%s) - start ))s"
echo "  ssh azureuser@$IP"
echo "  web UI: ssh -L 8000:127.0.0.1:8000 azureuser@$IP   then http://localhost:8000"
echo "  scheduler check: ssh azureuser@$IP bash /opt/opswatch/deploy/verify-timer.sh"
