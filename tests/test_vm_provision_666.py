"""Exercise provisioning decisions with fake OS/network/Hyper-V boundaries only."""
from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/vm/Install-VM666.ps1"
POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell 5.1 mock contract")


def ps(body):
    load = ". '" + str(SCRIPT).replace("'", "''") + "'\n"
    code = "$ErrorActionPreference = 'Stop'\n" + load + body
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", code],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def test_parser_and_dot_source_have_no_actions():
    assert ps(r"""function Get-CimInstance { throw 'unexpected inspection' }
$tokens = $null; $errors = $null
$null = [Management.Automation.Language.Parser]::ParseFile(
    'SCRIPT_PATH', [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
'loaded'
""".replace("SCRIPT_PATH", str(SCRIPT).replace("'", "''"))) == "loaded"


def test_pinned_plan():
    plan = json.loads(ps("Get-VM666Plan | ConvertTo-Json"))
    assert plan["Cpu"] == 4
    assert plan["MemoryBytes"] == 8 * 1024**3
    assert plan["DiskBytes"] == 64 * 1024**3
    assert plan["IsoBytes"] == 4080486400
    assert plan["Url"].startswith("https://releases.ubuntu.com/24.04/")
    assert plan["Sha256"] == "97f3d7ffb032c3eb3b23d2c8be9cc76e60c2c1f2c0146ba5ba9fe01cafae0fd8"
    assert plan["Protection"] == "UNVERIFIED"


@pytest.mark.parametrize("path", [r"C:\Windows", r"D:\LabHQ-VM666-other", r"D:\LabHQ-VM666\..\other"])
def test_paths_cannot_escape_fixture(path):
    assert ps(f"try {{ $null = Assert-VM666Path '{path}'; throw 'accepted' }} catch {{ $_.Exception.Message }}") == "Path outside the dedicated fixture root."


def test_reparse_path_refused():
    assert ps(r"""
function Test-Path { $true }
function Get-Item { [pscustomobject]@{Attributes = [IO.FileAttributes]::ReparsePoint} }
try { $null = Assert-VM666Path 'D:\LabHQ-VM666\disk.vhdx'; throw 'accepted' }
catch { $_.Exception.Message }
""") == "Reparse point in fixture path; refusing."


def test_no_automatic_elevation():
    assert "administrator PowerShell" in ps(r"""
function Test-VM666Administrator { $false }
function Get-WindowsOptionalFeature { throw 'queried before elevation check' }
try { Enable-VM666Feature; throw 'accepted' } catch { $_.Exception.Message }
""")


@pytest.mark.parametrize("state", ["Enabled", "EnablePending", "DisablePending", "Disabled"])
def test_feature_stage_never_restarts(state):
    output = ps(r"""
function Assert-VM666Administrator {}
function Get-WindowsOptionalFeature { [pscustomobject]@{State = 'STATE_VALUE'} }
function Enable-WindowsOptionalFeature {
    param([switch]$Online, [string]$FeatureName, [switch]$All, [switch]$NoRestart)
    if (-not ($Online -and $All -and $NoRestart) -or $FeatureName -ne 'Microsoft-Hyper-V-All') { throw 'bad feature request' }
    'enabled-without-restart'
}
function Restart-Computer { throw 'automatic restart' }
try { Enable-VM666Feature } catch { $_.Exception.Message }
""".replace("STATE_VALUE", state))
    if state == "Disabled":
        assert "enabled-without-restart" in output
    elif state == "Enabled":
        assert output == "Already enabled; no change."
    else:
        assert "reboot manually first" in output


@pytest.mark.parametrize("bad_size", [False, True])
def test_iso_mismatch_prevents_boot(bad_size):
    output = ps(r"""
function Assert-VM666Path($Path) { $Path }
function Get-Item { [pscustomobject]@{Length = SIZE_VALUE} }
function Get-FileHash { [pscustomobject]@{Hash = ('0' * 64)} }
try { Test-VM666Media 'D:\LabHQ-VM666\image.iso'; throw 'accepted' } catch { $_.Exception.Message }
""".replace("SIZE_VALUE", "1" if bad_size else "4080486400"))
    assert output.startswith("ISO size/hash mismatch")


# Every side-effect boundary is replaced: never download, create a real directory,
# modify ACLs, query Hyper-V, or start a VM from this suite.
PROVISION_MOCK = r"""
$script:events = [Collections.Generic.List[string]]::new()
$script:nic = $true
$script:integration = $true
function Assert-VM666Administrator {}
function Assert-VM666Capacity {}
function Assert-VM666Path($Path) { $Path }
function Test-Path { $false }
function New-VM666PrivateRoot { $script:events.Add('private-root') }
function Write-VM666State($State) { $script:events.Add('state:' + $State.Phase) }
function Invoke-WebRequest { $script:events.Add('download') }
function Test-VM666Media { $script:events.Add('hash') }
function Get-VM { }
function New-VM {
    param($Name, $Generation, $MemoryStartupBytes, $Path, $NewVHDPath, $NewVHDSizeBytes)
    if ($Generation -ne 2 -or $MemoryStartupBytes -ne 8GB -or $NewVHDSizeBytes -ne 64GB) { throw 'bad VM shape' }
    $script:events.Add('create')
    [pscustomobject]@{Name = $Name; Id = [guid]'11111111-1111-1111-1111-111111111111'; Path = $Path; State = 'Off'}
}
function Set-VM {
    param($VM, $AutomaticStartAction, $AutomaticStopAction, $AutomaticCheckpointsEnabled, $CheckpointType)
    if ($AutomaticStartAction -ne 'Nothing' -or $AutomaticStopAction -ne 'TurnOff' -or $AutomaticCheckpointsEnabled -or $CheckpointType -ne 'Disabled') { throw 'unsafe startup' }
}
function Set-VMProcessor { param($VM, $Count); if ($Count -ne 4) { throw 'bad CPU' } }
function Set-VMMemory { param($VM, $DynamicMemoryEnabled); if ($DynamicMemoryEnabled) { throw 'dynamic RAM' } }
function Get-VMNetworkAdapter { if ($script:nic) { 'adapter' } }
function Remove-VMNetworkAdapter { process { $script:nic = $false; $script:events.Add('remove-nic') } }
function Get-VMIntegrationService { if ($script:integration) { [pscustomobject]@{Enabled = $true} } }
function Disable-VMIntegrationService { process { $script:integration = $false } }
function Add-VMDvdDrive { [pscustomobject]@{Path = 'dvd'} }
function Set-VMFirmware {
    param($VM, $EnableSecureBoot, $SecureBootTemplate, $FirstBootDevice)
    if ($EnableSecureBoot -ne 'On' -or $SecureBootTemplate -ne 'MicrosoftUEFICertificateAuthority') { throw 'bad firmware' }
}
function Start-VM { throw 'unexpected boot' }
"""


def test_provision_orders_hash_before_vm_and_finishes_offline():
    events = json.loads(ps(PROVISION_MOCK + r"""
$null = New-VM666Fixture
$script:events | ConvertTo-Json
"""))
    assert events == ["private-root", "state:preparing", "download", "hash", "create", "state:preparing", "remove-nic", "state:prepared"]


def test_failed_download_is_quarantined_without_creating_vm():
    events = json.loads(ps(PROVISION_MOCK + r"""
function Invoke-WebRequest { throw 'TLS failure' }
try { $null = New-VM666Fixture; throw 'accepted' } catch { if ($_.Exception.Message -ne 'TLS failure') { throw } }
$script:events | ConvertTo-Json
"""))
    assert events == ["private-root", "state:preparing", "state:quarantined"]


def test_existing_unknown_fixture_never_adopted_or_overwritten():
    assert ps(PROVISION_MOCK + r"""
function Test-Path { $true }
function Read-VM666State { throw 'unknown root' }
try { $null = New-VM666Fixture; throw 'accepted' } catch { if ($_.Exception.Message -ne 'unknown root') { throw } }
if ($script:events.Count) { throw 'mutated unknown root' }
'refused'
""") == "refused"


def test_prepared_rerun_is_read_only():
    assert ps(PROVISION_MOCK + r"""
function Test-Path { $true }
function Read-VM666State { [pscustomobject]@{Phase = 'prepared'} }
function Get-VM666OwnedVM { [pscustomobject]@{State = 'Off'} }
$script:nic = $false; $script:integration = $false
$null = New-VM666Fixture
if ($script:events.Count) { throw 'rerun mutated fixture' }
'unchanged'
""") == "unchanged"


@pytest.mark.parametrize("state,nic,integration", [("Running", False, False), ("Off", True, False), ("Off", False, True)])
def test_rerun_refuses_changed_vm(state, nic, integration):
    body = PROVISION_MOCK + r"""
$script:nic = NIC; $script:integration = INTEGRATION
try { Assert-VM666Offline ([pscustomobject]@{State = 'STATE'}); throw 'accepted' }
catch { if ($_.Exception.Message -eq 'accepted') { throw }; 'refused' }
"""
    assert ps(body.replace("NIC", f"${str(nic).lower()}").replace("INTEGRATION", f"${str(integration).lower()}").replace("STATE", state)) == "refused"


def test_stop_requires_manifest_vm_id_and_retains_files():
    assert ps(r"""
function Assert-VM666Administrator {}
function Import-Module {}
function Read-VM666State { [pscustomobject]@{Phase = 'prepared'} }
function Get-VM666OwnedVM { [pscustomobject]@{Id = 'owned-id'; State = 'Running'} }
function Stop-VM { throw 'wrong VM stopped' }
function Remove-VM { throw 'wrong VM removed' }
function Remove-Item { throw 'disk deleted' }
try { Stop-VM666Fixture 'different-id' $true; throw 'accepted' }
catch { $_.Exception.Message }
""") == "Supply the exact fixture.json VmId; no other VM is touched."


def test_configuration_removal_requires_separate_destructive_confirmation():
    assert ps(r"""
function Assert-VM666Administrator {}
function Import-Module {}
function Read-VM666State { [pscustomobject]@{Phase = 'prepared'} }
function Get-VM666OwnedVM { [pscustomobject]@{Id = 'owned-id'; State = 'Off'} }
function Assert-VM666Offline {}
function Remove-VM { throw 'VM configuration deleted without confirmation' }
function Write-VM666State { throw 'state changed without confirmation' }
try { Stop-VM666Fixture 'owned-id' $true; throw 'accepted' }
catch { $_.Exception.Message }
""") == "RemoveVM deletes VM configuration and checkpoints; supply -ConfirmConfigurationRemoval after backup."


@pytest.mark.parametrize("phase", ["preparing", "quarantined", "removed"])
def test_incomplete_manifest_never_looks_up_vm(phase):
    assert ps(r"""
function Get-VM { throw 'looked up incomplete VM' }
try { Get-VM666OwnedVM ([pscustomobject]@{Phase = 'PHASE'; VmId = ''}); throw 'accepted' }
catch { $_.Exception.Message }
""".replace("PHASE", phase)) == "Incomplete/quarantined fixture; manual reconciliation required."


@pytest.mark.parametrize("remove", [False, True])
def test_confirmed_cleanup_targets_only_owned_vm_and_retains_disks(remove):
    events = json.loads(ps(r"""
$script:events = [Collections.Generic.List[string]]::new()
function Assert-VM666Administrator {}
function Import-Module {}
function Read-VM666State { [pscustomobject]@{Phase = 'prepared'} }
function Get-VM666OwnedVM { [pscustomobject]@{Id = 'owned-id'; State = 'Running'} }
function Assert-VM666Offline { $script:events.Add('offline') }
function Write-VM666State($State) { $script:events.Add('state:' + $State.Phase) }
function Remove-Item { throw 'disk deletion forbidden' }
function Remove-VM { param($VM, [switch]$Force); if ($VM.Id -ne 'owned-id') { throw 'wrong VM' }; $script:events.Add('remove-configuration') }
function Stop-VM { param($VM, [switch]$TurnOff, [switch]$Force); if ($VM.Id -ne 'owned-id' -or -not $TurnOff) { throw 'wrong stop' }; $script:events.Add('stop') }
$message = Stop-VM666Fixture 'owned-id' REMOVE -ConfirmConfigurationRemoval
if (REMOVE -and $message -notlike 'VM configuration deleted; checkpoints deleted/merged*') { throw 'misleading removal result' }
ConvertTo-Json -InputObject @($script:events)
""".replace("REMOVE", f"${str(remove).lower()}")))
    assert events == (["offline", "remove-configuration", "state:removed"] if remove else ["stop"])


ACL_MOCK = r"""
$script:acl = [Security.AccessControl.DirectorySecurity]::new()
$script:acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
$script:acl.SetAccessRuleProtection($true, $false)
function Get-Acl { $script:acl }
function Assert-VM666Path($Path) { $Path }
"""


@pytest.mark.parametrize("rights", ["DeleteSubdirectoriesAndFiles", "ChangePermissions", "TakeOwnership"])
def test_untrusted_parent_replacement_rights_block_provisioning(rights):
    assert ps(ACL_MOCK + r"""
$script:acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-5-11'), 'RIGHTS', 'Allow'))
try { Assert-VM666Parent; throw 'accepted' } catch { $_.Exception.Message }
""".replace("RIGHTS", rights)) == "Untrusted principal can replace the volume trust anchor; no ACL changes made."


def test_volume_child_creation_alone_does_not_allow_private_child_replacement():
    assert ps(ACL_MOCK + r"""
$script:acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-5-11'), 'CreateDirectories', 'Allow'))
Assert-VM666Parent
'allowed'
""") == "allowed"


def test_private_root_rejects_even_untrusted_read_access():
    assert ps(ACL_MOCK + r"""
$script:acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-5-11'), 'ReadAndExecute', 'Allow'))
try { Assert-VM666Private 'D:\LabHQ-VM666'; throw 'accepted' } catch { $_.Exception.Message }
""") == "Unexpected access to the private fixture root."
