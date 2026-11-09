#!/usr/bin/env bash
# Gives ops-ctl its own admin SSH key and authorizes it for azureuser on web01, app01 and stor01.
# Run from Azure Cloud Shell (or any machine with `az`) after main.bicep is deployed, before site.yml.
#
# Why: Ansible on ops-ctl needs to log into the other VMs as azureuser. Agent forwarding (`ssh -A`)
# from a Windows laptop's built-in OpenSSH can list the key but fails to sign with it, so ops-ctl
# gets a key of its own. The private key never leaves ops-ctl; Azure's VM agent does both steps,
# so no working SSH login is needed. Safe to re-run: an existing key is kept, not regenerated.
set -euo pipefail

RG=${RG:-rg-opswatch-lab}
PREFIX=opswatch
ADMIN=azureuser

echo "Creating (or reusing) the admin key on ops-ctl..."
OPSCTL_KEY=$(az vm run-command invoke -g "$RG" -n "$PREFIX-ops-ctl" --command-id RunShellScript \
  --scripts "k=/home/$ADMIN/.ssh/id_ed25519; [ -f \$k ] || sudo -u $ADMIN ssh-keygen -q -t ed25519 -N '' -C ansible@ops-ctl -f \$k; cat \$k.pub" \
  --query "value[0].message" -o tsv | grep -o "ssh-ed25519 [A-Za-z0-9+/=]* .*" | head -1)

if [ -z "$OPSCTL_KEY" ]; then
  echo "Could not read ops-ctl's public key." >&2
  exit 1
fi
echo "ops-ctl key: $OPSCTL_KEY"

for vm in web01 app01 stor01; do
  az vm user update -g "$RG" -n "$PREFIX-$vm" -u "$ADMIN" --ssh-key-value "$OPSCTL_KEY" -o none
  echo "$vm: key added"
done
