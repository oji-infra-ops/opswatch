# OpsWatch lab on Azure

Four Ubuntu 24.04 VMs on one private subnet (`10.10.1.0/24`). Only `ops-ctl` has a public IP, and the NSG allows SSH to it from one address only. The other three are reached from ops-ctl over the private network.

| VM      | Private IP  | Extra                    |
|---------|-------------|--------------------------|
| ops-ctl | 10.10.1.10  | Public IP, runs OpsWatch |
| web01   | 10.10.1.11  |                          |
| app01   | 10.10.1.12  |                          |
| stor01  | 10.10.1.13  | 4 GiB data disk at LUN 0 |

## Cost

Default size is `Standard_B1s`. Four of them running all month, plus disks and one public IP, is roughly US$35–45/month in most US regions. Every VM auto-shuts down daily at `shutdownTime`; a deallocated VM stops compute charges (disks and the static IP still bill). Delete the resource group when the project is done.

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
     shutdownTimeZone="Eastern Standard Time"
az deployment group show -g $RG -n main --query properties.outputs
```

`adminPublicKey` must be the public key of the machine you'll SSH **from** (your laptop). If you don't have one: `ssh-keygen -t ed25519`.

## Daily use

```bash
az vm start      --ids $(az vm list -g $RG --query "[].id" -o tsv)   # morning
ssh -A azureuser@<ops-ctl public IP>                                   # -A forwards your key for Ansible
az vm deallocate --ids $(az vm list -g $RG --query "[].id" -o tsv)   # evening, if before auto-shutdown
```

## Rebuild from nothing

```bash
az group delete -n $RG --yes
# then Deploy again, then run lab/ansible/site.yml from ops-ctl
```
