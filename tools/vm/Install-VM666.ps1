#requires -Version 5.1
<#!
Disposable fixture preparation only. No engine launch or protection verdict.
Dot-source to load functions without performing an action (used by mock tests).
#>
[CmdletBinding()]
param([ValidateSet('Inspect', 'EnableFeature', 'Provision', 'Stop', 'Unregister')]
      [string]$Action = 'Inspect', [string]$ConfirmVmId = '')

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-VM666Plan {
    [pscustomobject]@{
        Schema = 1
        Root = 'D:\LabHQ-VM666'
        Name = 'LabHQ-666-Ubuntu2404'
        Iso = 'ubuntu-24.04.5-live-server-amd64.iso'
        Url = 'https://releases.ubuntu.com/24.04/ubuntu-24.04.5-live-server-amd64.iso'
        Sha256 = '97f3d7ffb032c3eb3b23d2c8be9cc76e60c2c1f2c0146ba5ba9fe01cafae0fd8'
        IsoBytes = [long]4080486400
        Cpu = 4
        MemoryBytes = [long]8GB
        DiskBytes = [long]64GB
        MinimumFreeBytes = [long]80GB
        Protection = 'UNVERIFIED'
    }
}

function Test-VM666Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-VM666Administrator {
    if (-not (Test-VM666Administrator)) {
        throw 'Use an administrator PowerShell window manually; no automatic elevation.'
    }
}

function Assert-VM666Path([string]$Path) {
    $plan = Get-VM666Plan
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -ne $plan.Root -and -not $full.StartsWith($plan.Root + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Path outside the dedicated fixture root.'
    }
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Reparse point in fixture path; refusing.'
            }
        }
        $parent = Split-Path -Path $cursor -Parent
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    $full
}

function Assert-VM666Parent {
    # Creating files in the volume root is permitted. Replacing this child is not.
    $acl = Get-Acl -LiteralPath 'D:\'
    $trusted = @('S-1-5-18', 'S-1-5-32-544')
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) {
        throw 'Volume owner can replace the trust anchor; use a separately managed volume.'
    }
    foreach ($ace in $acl.Access) {
        if ($ace.AccessControlType -ne 'Allow' -or ($ace.PropagationFlags -band 2)) { continue }
        $sid = $ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        $danger = [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
                  [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
                  [Security.AccessControl.FileSystemRights]::TakeOwnership
        if ($sid -notin $trusted -and ($ace.FileSystemRights -band $danger)) {
            throw 'Untrusted principal can replace the volume trust anchor; no ACL changes made.'
        }
    }
}

function Assert-VM666Private([string]$Path) {
    $null = Assert-VM666Path $Path
    $acl = Get-Acl -LiteralPath $Path
    $trusted = @('S-1-5-18', 'S-1-5-32-544')
    if (-not $acl.AreAccessRulesProtected -or
        $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) {
        throw 'Fixture root ACL/owner is not private.'
    }
    foreach ($ace in $acl.Access) {
        $sid = $ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($ace.AccessControlType -eq 'Allow' -and $sid -notin $trusted) {
            throw 'Unexpected access to the private fixture root.'
        }
    }
}

function New-VM666PrivateRoot {
    $plan = Get-VM666Plan
    $null = Assert-VM666Path $plan.Root
    Assert-VM666Parent
    if (Test-Path -LiteralPath $plan.Root) { throw 'Existing fixture root; inspect state, never overwrite.' }
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl',
            'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    # Windows PowerShell 5.1/.NET Framework: apply the private ACL at creation.
    $null = [IO.Directory]::CreateDirectory($plan.Root, $acl)
    Assert-VM666Private $plan.Root
}

function Write-VM666State($State) {
    $plan = Get-VM666Plan
    $path = Assert-VM666Path (Join-Path $plan.Root 'fixture.json')
    [IO.File]::WriteAllText($path, ($State | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
}

function Read-VM666State {
    $plan = Get-VM666Plan
    Assert-VM666Parent
    Assert-VM666Private $plan.Root
    $path = Assert-VM666Path (Join-Path $plan.Root 'fixture.json')
    $state = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if ($state.Schema -ne 1 -or $state.Root -ne $plan.Root -or $state.Name -ne $plan.Name) {
        throw 'Fixture manifest identity mismatch.'
    }
    $state
}

function Get-VM666OwnedVM($State) {
    if ($State.Phase -ne 'prepared' -or -not $State.VmId) { throw 'Incomplete/quarantined fixture; manual reconciliation required.' }
    $vm = Get-VM -Id ([guid]$State.VmId) -ErrorAction Stop
    $null = Assert-VM666Path $vm.Path
    if ($vm.Name -ne (Get-VM666Plan).Name) { throw 'VM identity mismatch.' }
    $vm
}

function Test-VM666Media([string]$Path) {
    $plan = Get-VM666Plan
    $null = Assert-VM666Path $Path
    if ((Get-Item -LiteralPath $Path).Length -ne $plan.IsoBytes -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $plan.Sha256) {
        throw 'ISO size/hash mismatch; preserve fixture for inspection, never boot.'
    }
}

function Get-VM666Host {
    $plan = Get-VM666Plan
    $os = Get-CimInstance Win32_OperatingSystem
    $cpu = Get-CimInstance Win32_ComputerSystem
    $elevated = Test-VM666Administrator
    $feature = 'UNKNOWN: administrator query required'
    if ($elevated) { $feature = [string](Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All).State }
    $anchor = 'UNVERIFIED'
    try { Assert-VM666Parent; $anchor = 'static ACL check only' } catch { $anchor = $_.Exception.Message }
    [pscustomobject]@{
        Plan = $plan; Windows = $os.Caption; Build = $os.Version
        RamBytes = [long]$os.TotalVisibleMemorySize * 1KB
        LogicalCpu = $cpu.NumberOfLogicalProcessors; HypervisorPresent = $cpu.HypervisorPresent
        FreeBytes = (Get-PSDrive D).Free; Elevated = $elevated
        HyperVFeature = $feature; HyperVCmdlets = [bool](Get-Command Get-VM -ErrorAction SilentlyContinue)
        ParentAnchor = $anchor
        SameLoginAdministratorIsolation = 'UNSUPPORTED: use a separate standard engine principal'
        Protection = 'UNVERIFIED'
    }
}

function Enable-VM666Feature {
    Assert-VM666Administrator
    $feature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All
    if ([string]$feature.State -eq 'Enabled') { return 'Already enabled; no change.' }
    if ([string]$feature.State -match 'Pending') { throw 'Feature change pending; save work and reboot manually first.' }
    Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart
    'Save work; if RestartNeeded is true, reboot manually. Do not provision before the reboot.'
}

function Assert-VM666Capacity {
    $plan = Get-VM666Plan
    $os = Get-CimInstance Win32_OperatingSystem
    if ($os.Caption -notmatch 'Windows 1[01] (Pro|Enterprise|Education)') { throw 'Unsupported Windows edition.' }
    if ([long]$os.TotalVisibleMemorySize * 1KB -lt 16GB -or (Get-PSDrive D).Free -lt $plan.MinimumFreeBytes) {
        throw 'Need 16 GiB host RAM and 80 GiB free on D:.'
    }
    if ([string](Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All).State -ne 'Enabled') {
        throw 'Hyper-V is not enabled; complete the feature/reboot stage first.'
    }
    Import-Module Hyper-V -ErrorAction Stop
    if ((Get-Service vmms).Status -ne 'Running') { throw 'VM management service unavailable; inspect/reboot manually.' }
}

function New-VM666Fixture {
    Assert-VM666Administrator
    Assert-VM666Capacity
    $plan = Get-VM666Plan
    if (Test-Path -LiteralPath $plan.Root) {
        $state = Read-VM666State
        $vm = Get-VM666OwnedVM $state
        Assert-VM666Offline $vm
        return 'Existing prepared fixture unchanged; no download/start/repair.'
    }
    if (@(Get-VM | Where-Object Name -eq $plan.Name).Count) { throw 'VM name already exists; no adoption.' }
    New-VM666PrivateRoot
    $state = [ordered]@{ Schema = 1; Root = $plan.Root; Name = $plan.Name; Phase = 'preparing'; VmId = ''; Protection = 'UNVERIFIED' }
    Write-VM666State $state
    try {
        $iso = Assert-VM666Path (Join-Path $plan.Root $plan.Iso)
        # No certificate validation bypass; failure leaves an unbootable partial fixture.
        Invoke-WebRequest -UseBasicParsing -Uri $plan.Url -OutFile $iso -TimeoutSec 3600
        Test-VM666Media $iso
        $config = Assert-VM666Path (Join-Path $plan.Root 'config')
        $disk = Assert-VM666Path (Join-Path $plan.Root 'disk.vhdx')
        $vm = New-VM -Name $plan.Name -Generation 2 -MemoryStartupBytes $plan.MemoryBytes -Path $config -NewVHDPath $disk -NewVHDSizeBytes $plan.DiskBytes
        $state.VmId = [string]$vm.Id
        Write-VM666State $state
        Set-VM -VM $vm -AutomaticStartAction Nothing -AutomaticStopAction TurnOff -AutomaticCheckpointsEnabled $false -CheckpointType Disabled
        Set-VMProcessor -VM $vm -Count $plan.Cpu
        Set-VMMemory -VM $vm -DynamicMemoryEnabled $false
        Get-VMNetworkAdapter -VM $vm | Remove-VMNetworkAdapter
        # Disable host/guest integration channels for the offline fixture.
        Get-VMIntegrationService -VM $vm | Disable-VMIntegrationService
        $dvd = Add-VMDvdDrive -VM $vm -Path $iso -Passthru
        Set-VMFirmware -VM $vm -EnableSecureBoot On -SecureBootTemplate MicrosoftUEFICertificateAuthority -FirstBootDevice $dvd
        Assert-VM666Offline $vm
        $state.Phase = 'prepared'
        Write-VM666State $state
        'Prepared, powered off, no network adapter. Install guest manually via administrator console; protection UNVERIFIED.'
    } catch {
        $state.Phase = 'quarantined'
        Write-VM666State $state
        throw
    }
}

function Assert-VM666Offline($Vm) {
    if ([string]$Vm.State -ne 'Off' -or @(Get-VMNetworkAdapter -VM $Vm).Count) {
        throw 'Fixture must be powered off with zero network adapters; no automatic repair.'
    }
    if (@(Get-VMIntegrationService -VM $Vm | Where-Object Enabled).Count) {
        throw 'Unexpected enabled integration channel.'
    }
}

function Stop-VM666Fixture([string]$ExpectedId, [bool]$Unregister) {
    Assert-VM666Administrator
    Import-Module Hyper-V -ErrorAction Stop
    $state = Read-VM666State
    $vm = Get-VM666OwnedVM $state
    if ($ExpectedId -ne [string]$vm.Id) { throw 'Supply the exact fixture.json VmId; no other VM is touched.' }
    if ($Unregister) {
        Assert-VM666Offline $vm
        Remove-VM -VM $vm -Force
        $state.Phase = 'unregistered'
        Write-VM666State $state
        'VM unregistered; ISO/VHD/state retained. No recursive deletion or Hyper-V feature removal.'
    } elseif ([string]$vm.State -ne 'Off') {
        Stop-VM -VM $vm -TurnOff -Force
        'Disposable VM powered off; guest data may be lost. Cleanup/protection still UNVERIFIED.'
    } else { 'Already off; no change.' }
}

if ($MyInvocation.InvocationName -ne '.') {
    switch ($Action) {
        'Inspect' { Get-VM666Host | ConvertTo-Json -Depth 5 }
        'EnableFeature' { Enable-VM666Feature }
        'Provision' { New-VM666Fixture }
        'Stop' { Stop-VM666Fixture $ConfirmVmId $false }
        'Unregister' { Stop-VM666Fixture $ConfirmVmId $true }
    }
}
