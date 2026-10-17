# Rigbook hardware scan for Windows (PowerShell 5.1+). Prints one JSON document.
#   powershell -ExecutionPolicy Bypass -c "irm http://<rigbook>/scan.ps1 | iex"
# When downloaded from Rigbook, the result is also sent back to it for review.
$ErrorActionPreference = 'Stop'
$SubmitUrl = '__SUBMIT_URL__'
# Each section is collected on its own so one failing cmdlet doesn't lose the rest
function T([scriptblock]$b) { try { & $b } catch { $null } }
$cs = T { Get-CimInstance Win32_ComputerSystem }
$gpuMem = @{}
T { Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}' |
  ForEach-Object { $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
    if ($p.DriverDesc -and $p.'HardwareInformation.qwMemorySize') { $gpuMem[$p.DriverDesc] = [int64]$p.'HardwareInformation.qwMemorySize' } } }
$r = [ordered]@{ rigbook = 'windows'; hostname = $env:COMPUTERNAME; domain = '' }
if ($cs -and $cs.PartOfDomain) { $r.domain = $cs.Domain }
$r.system    = T { $cs | Select-Object Manufacturer, Model, SystemSKUNumber, SystemFamily }
$r.bios      = T { Get-CimInstance Win32_BIOS | Select-Object SerialNumber }
$r.board     = T { Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product, SerialNumber }
$r.chassis   = T { @((Get-CimInstance Win32_SystemEnclosure).ChassisTypes) }
$r.os        = T { Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version }
$r.cpus      = T { @(Get-CimInstance Win32_Processor | Select-Object Name, Manufacturer, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, SocketDesignation) }
$r.memory    = T { @(Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity, SMBIOSMemoryType, FormFactor, Speed, Manufacturer, PartNumber, SerialNumber, DeviceLocator, BankLabel) }
$r.disks     = T { @(Get-PhysicalDisk | Select-Object FriendlyName, SerialNumber, Size, @{n='MediaType';e={[string]$_.MediaType}}, @{n='BusType';e={[string]$_.BusType}}) }
$r.diskdrive = T { @(Get-CimInstance Win32_DiskDrive | Select-Object Model, SerialNumber, Size, InterfaceType) }
$r.gpus      = T { @(Get-CimInstance Win32_VideoController | Where-Object { $_.PNPDeviceID -like 'PCI\*' } |
                     ForEach-Object { [ordered]@{ Name = $_.Name; Vendor = $_.AdapterCompatibility; Memory = $gpuMem[$_.Name] } }) }
$r.ips       = T { @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | ForEach-Object { $_.IPAddress }) }
$r.macs      = T { @(Get-NetAdapter -Physical | Where-Object { $_.MacAddress } | ForEach-Object { $_.MacAddress }) }
$json = $r | ConvertTo-Json -Depth 4 -Compress
$json
if ($SubmitUrl -like 'http*') {
  try { Invoke-RestMethod -Method Post -Uri $SubmitUrl -Body $json -ContentType 'application/json' | Out-Null
        Write-Host 'Sent to Rigbook, open it to review this scan.' }
  catch { Write-Host "Could not send to $SubmitUrl" }
}
