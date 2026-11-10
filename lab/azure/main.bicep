// OpsWatch lab: four Ubuntu 24.04 VMs on one private subnet.
// Only ops-ctl has a public IP, and SSH to it is allowed from one source address.
targetScope = 'resourceGroup'

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Your SSH public key (contents of ~/.ssh/id_ed25519.pub). Installed for the admin user on every VM.')
param adminPublicKey string

@description('The only address allowed to SSH to ops-ctl, in CIDR form, e.g. 203.0.113.7/32.')
param allowedSshSource string

@description('Admin user on every VM.')
param adminUsername string = 'azureuser'

@description('VM size for all four VMs.')
param vmSize string = 'Standard_B1s'

@description('Disk controller. Newer sizes (v6/v7) may support NVMe only.')
@allowed([ 'SCSI', 'NVMe' ])
param diskControllerType string = 'SCSI'

@description('Daily auto-shutdown time, 24h HHmm, in the time zone below.')
param shutdownTime string = '2300'

@description('Windows time zone ID for auto-shutdown, e.g. "Eastern Standard Time" or "UTC".')
param shutdownTimeZone string = 'UTC'

var prefix = 'opswatch'
var vms = [
  { name: 'ops-ctl', ip: '10.10.1.10', publicIp: true, dataDiskGb: 0 }
  { name: 'web01', ip: '10.10.1.11', publicIp: false, dataDiskGb: 0 }
  { name: 'app01', ip: '10.10.1.12', publicIp: false, dataDiskGb: 0 }
  { name: 'stor01', ip: '10.10.1.13', publicIp: false, dataDiskGb: 4 }
]

resource nsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: '${prefix}-nsg'
  location: location
  properties: {
    securityRules: [
      {
        name: 'ssh-to-ops-ctl-from-admin'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: allowedSshSource
          sourcePortRange: '*'
          destinationAddressPrefix: vms[0].ip
          destinationPortRange: '22'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: '${prefix}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: [ '10.10.0.0/16' ] }
    subnets: [
      {
        name: 'lab'
        properties: {
          addressPrefix: '10.10.1.0/24'
          networkSecurityGroup: { id: nsg.id }
        }
      }
    ]
  }
}

resource pip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: '${prefix}-ops-ctl-pip'
  location: location
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource nics 'Microsoft.Network/networkInterfaces@2023-11-01' = [for vm in vms: {
  name: '${prefix}-${vm.name}-nic'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: vnet.properties.subnets[0].id }
          privateIPAllocationMethod: 'Static'
          privateIPAddress: vm.ip
          publicIPAddress: vm.publicIp ? { id: pip.id } : null
        }
      }
    ]
  }
}]

resource machines 'Microsoft.Compute/virtualMachines@2024-03-01' = [for (vm, i) in vms: {
  name: '${prefix}-${vm.name}'
  location: location
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: vm.name
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            { path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: adminPublicKey }
          ]
        }
      }
    }
    storageProfile: {
      diskControllerType: diskControllerType
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: { storageAccountType: 'Standard_LRS' }
        deleteOption: 'Delete'
      }
      dataDisks: vm.dataDiskGb > 0 ? [
        {
          lun: 0
          createOption: 'Empty'
          diskSizeGB: vm.dataDiskGb
          managedDisk: { storageAccountType: 'Standard_LRS' }
          deleteOption: 'Delete'
        }
      ] : []
    }
    networkProfile: {
      networkInterfaces: [ { id: nics[i].id, properties: { deleteOption: 'Delete' } } ]
    }
  }
}]

resource shutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = [for (vm, i) in vms: {
  name: 'shutdown-computevm-${prefix}-${vm.name}'
  location: location
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: { time: shutdownTime }
    timeZoneId: shutdownTimeZone
    targetResourceId: machines[i].id
    notificationSettings: { status: 'Disabled' }
  }
}]

output opsCtlPublicIp string = pip.properties.ipAddress
output sshCommand string = 'ssh ${adminUsername}@${pip.properties.ipAddress}'
