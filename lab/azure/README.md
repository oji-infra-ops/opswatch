# OpsWatch lab on Azure

Four Ubuntu 24.04 VMs on one private subnet (`10.10.1.0/24`). Only `ops-ctl` has a public IP, and the NSG allows SSH to it from one address only. The other three are reached from ops-ctl over the private network.

| VM      | Private IP  | Extra                    |
|---------|-------------|--------------------------|
| ops-ctl | 10.10.1.10  | Public IP, runs OpsWatch |
| web01   | 10.10.1.11  |                          |
| app01   | 10.10.1.12  |                          |
| stor01  | 10.10.1.13  | 4 GiB data disk at LUN 0 |

## Cost

Default size is `Standard_B1s`, but many subscriptions can't get B-series capacity or quota. The working lab uses `vmSize=Standard_F1als_v7` in `eastus`, which only supports NVMe disks, so it also needs `diskControllerType=NVMe`. Check current prices in the Azure pricing calculator before leaving all four VMs running. Every VM auto-shuts down daily at `shutdownTime`; a deallocated VM stops compute charges (disks and the static IP still bill). Delete the resource group when the project is done.

Set a budget alert once: Portal → Cost Management → Budgets → e.g. US$25 with an email at 80%.

## Deploy (from Azure Cloud Shell or any machine with `az`)

```bash
az login                                   # skip in Cloud Shell
RG=rg-opswatch-lab
az group create -n $RG -l eastus
MYIP=$(curl -s https://api.ipify.org)      # run this on YOUR laptop, not Cloud Shell, and paste it below
az deployment group create -g $RG -f lab/azure/main.bicep \
  -p adminPublicKey="$(cat ~/.ssh/id_ed25519.pub)" \
     allowedSshSource="<your-laptop-ip>/32" \
     shutdownTimeZone="Pacific Standard Time" \
     vmSize=Standard_F1als_v7 diskControllerType=NVMe
az deployment group show -g $RG -n main --query properties.outputs
```

`adminPublicKey` must be the public key of the machine you'll SSH **from** (your laptop). If you don't have one: `ssh-keygen -t ed25519`. If you already have one, don't run `ssh-keygen` again: it overwrites the key the VMs trust.

## Give ops-ctl its own admin key (once per deploy)

```bash
bash lab/azure/bootstrap-admin-key.sh
```

Ansible runs on ops-ctl and logs into the other three VMs as `azureuser`. Agent forwarding (`ssh -A`) from Windows' built-in OpenSSH lists your key but can't sign with it, so ops-ctl gets a key of its own. The script creates it on ops-ctl and adds its public half to the other VMs through the Azure VM agent. The private key never leaves ops-ctl.

## Configure the VMs (on ops-ctl)

```bash
sudo apt-get update && sudo apt-get install -y ansible git
git clone -b spec-v1-implementation https://github.com/oji-infra-ops/opswatch.git && cd opswatch
ansible all -i lab/ansible/hosts.ini -m ping             # all four must answer pong
ansible-playbook -i lab/ansible/hosts.ini lab/ansible/site.yml
```

## Daily use

```bash
az vm start      --ids $(az vm list -g $RG --query "[].id" -o tsv)   # morning
ssh azureuser@<ops-ctl public IP>
az vm deallocate --ids $(az vm list -g $RG --query "[].id" -o tsv)   # evening, if before auto-shutdown
```

## Rebuild from nothing

```bash
az group delete -n $RG --yes
# then Deploy, bootstrap-admin-key.sh, and Configure the VMs again
```

If SSH to ops-ctl times out, your home IP has probably changed. Update the NSG rule:

```bash
az network nsg rule update -g $RG --nsg-name opswatch-nsg -n ssh-to-ops-ctl-from-admin \
  --source-address-prefixes <new-laptop-ip>/32
```

## OpsWatch itself

The last play in `site.yml` deploys OpsWatch on ops-ctl: code in `/opt/opswatch` (git checkout, venv), database in `/var/lib/opswatch/opswatch.db`, the 5-minute timer and the web UI as systemd units, and `/etc/profile.d/opswatch.sh` so manual runs use the timer's database and lock. Pin a release with `-e opswatch_version=v1.0`. Check it with `bash /opt/opswatch/deploy/verify-timer.sh`.

The web UI listens on `127.0.0.1:8000` only (no login). From your laptop: `ssh -L 8000:127.0.0.1:8000 azureuser@<ops-ctl public IP>`, then open http://localhost:8000.

## Rebuild everything with one command (Cloud Shell)

```bash
ALLOWED_IP=<your-laptop-ip> PUBKEY_FILE=~/id_ed25519.pub bash lab/azure/rebuild.sh
```

Deletes `rg-opswatch-lab` if it exists (asks first), deploys the VMs, gives ops-ctl its admin key, and runs the whole playbook on ops-ctl through the Azure VM agent. It prints the new SSH command and the total time.

If SSH to ops-ctl times out, your home IP has probably changed. Update the NSG rule:

```bash
az network nsg rule update -g $RG --nsg-name opswatch-nsg -n ssh-to-ops-ctl-from-admin \
  --source-address-prefixes <new-laptop-ip>/32
```

## Web UI (on ops-ctl)

```bash
cd ~/opswatch && .venv/bin/pip install -q -e ".[web]"
sudo cp deploy/systemd/opswatch-web.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now opswatch-web
```

It listens on `127.0.0.1:8000` only (no login). From your laptop: `ssh -L 8000:127.0.0.1:8000 azureuser@<ops-ctl public IP>`, then open http://localhost:8000.
