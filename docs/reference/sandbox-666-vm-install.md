# #666 VM 설치 준비 — 조회용

**설치 스크립트와 mock 시험을 검토하는 단계다.** 실제 설치와 guest 보호 시험은 미실행이며 `UNVERIFIED`다. 기본 off·일반 CLI 미연결을 유지한다.

## 사용자가 할 일

작업을 저장한 뒤 **Windows PowerShell 5.1을 관리자 권한으로 직접** 연다. 아래는 로컬 checkout에서 실행한다. `Bypass`는 해당 PowerShell 프로세스에만 적용되며 시스템 execution policy는 바꾸지 않는다.

```powershell
# 먼저 검토한 labhq checkout으로 이동한다.
Set-Location '<labhq-checkout>'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\vm\Install-VM666.ps1 -Action Inspect
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\vm\Install-VM666.ps1 -Action EnableFeature
```

`RestartNeeded=True` 또는 pending이면 **여기서 멈춘다**. 작업 저장 후 사용자가 재부팅한다. 자동 UAC·재부팅은 없다. 기능이 Enabled이고 `vmms`가 Running인 상태에서 다음 명령을 따로 실행한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\vm\Install-VM666.ps1 -Action Provision
```

| 산출·용량 | 값 |
|---|---|
| 저장소 | `D:\LabHQ-VM666`, 새 디렉터리만 생성. SYSTEM·Administrators 전용 ACL |
| VM | `LabHQ-666-Ubuntu2404`, Gen2·4 CPU·고정8GiB RAM·동적64GiB VHDX |
| 다운로드 | Ubuntu 24.04.5 Server amd64 ISO, 4,080,486,400 bytes(약3.80GiB) |
| 여유 조건 | D: 80GiB 이상. Hyper-V 기능 설치의 추가 다운로드 용량은 Windows 상태에 따라 달라 미확인 |
| 완료 상태 | 전원 OFF, NIC 0, integration service 전체 off, 자동 시작·checkpoint off |
| 재실행 | 완료 fixture는 읽기 검증만. 기존 이름/미등록 root/부분 실패는 덮어쓰지 않고 중단 |

관리자는 Hyper-V Manager에서 해당 VM console을 열어 시작하고 Ubuntu를 수동 설치한다. 네트워크·SSH·provider 로그인 없이 ISO의 기본 설치를 사용한다. guest 관리 계정은 신뢰 관리자 전용이며 엔진에는 주지 않는다. guest 패키지·supervisor·observer·broker 설치는 후속 범위다. 현재 스크립트가 Ubuntu 설치나 경계 시험까지 자동 완료하지는 않는다.

## 권한·중단·정리

- **관리자 로그인 계정과 같은 계정의 공격에 대한 보호는 UNSUPPORTED**다. 향후 엔진은 별도 standard Windows principal/guest 비특권 UID로 실행하고 VM 관리 그룹·VHD·console·root·snapshot 접근 거절을 실제 확인해야 한다. 서비스/broker provisioning은 아직 없다.
- D: 부모 ACL에 비신뢰 주체의 `DELETE_CHILD`·`WRITE_DAC`·`WRITE_OWNER`가 있으면 중단한다. 부모 ACL은 자동 수정하지 않는다. 대안은 별도 관리 volume/host다. reparse 경로·외부 경로·hash 불일치·부분 설치도 거절한다. 정적 ACL 검사는 effective-access 증명이 아니다.
- 실패하면 `fixture.json`의 `quarantined` 상태와 디스크를 보존한다. 자동 복구/재다운로드/타 VM 변경은 없다. 준비 중 manifest 생성 전 실패도 기존 root로 남겨 재사용을 거절한다.
- 정리: 관리자가 `D:\LabHQ-VM666\fixture.json`의 `VmId`를 확인하고 `<VmId>`를 교체한다. Stop은 강제 전원 종료이므로 guest 저장을 먼저 끝낸다.
- **RemoveVM은 되돌릴 수 없는 VM 삭제다.** 전원 OFF·NIC0·integration off 확인 후 VM 설정을 삭제하고 checkpoint를 삭제·VHD에 병합한다. 원래 firmware·격리 설정 복구에는 사전 export/백업이 필요하다. 백업 확인 후 `-ConfirmConfigurationRemoval`을 직접 지정한다. ISO/VHD/state와 Hyper-V 기능은 남으며 디스크 파일 삭제는 별도다. ([Remove-VM 공식 동작](https://learn.microsoft.com/en-us/powershell/module/hyper-v/remove-vm))

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\vm\Install-VM666.ps1 -Action Stop -ConfirmVmId '<VmId>'
# VM 설정·checkpoint 삭제를 승인할 때만 실행한다. 먼저 필요한 VM export/백업을 보관한다.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\vm\Install-VM666.ps1 -Action RemoveVM -ConfirmVmId '<VmId>' -ConfirmConfigurationRemoval
```

## 검증·출처

`Inspect`는 host 정보와 권한 상태를 읽는다. 비승격 세션에서는 Hyper-V 전체 기능 상태를 확정하지 않는다. 부모 ACL 결과도 정적 검사이며 실제 접근 거절의 증거가 아니다.

`python -m pytest tests/test_vm_provision_666.py -q`는 OS/network/Hyper-V 변경을 mock한다. 실제 설치·ACL 생성·Secure Boot 부팅·guest·종료/회수·독립 observer 결과는 검증하지 않는다.

- [Microsoft 설치 절차](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v), [New-VM](https://learn.microsoft.com/en-us/powershell/module/hyper-v/new-vm), [Set-VMFirmware](https://learn.microsoft.com/en-us/powershell/module/hyper-v/set-vmfirmware).
- [공식 ISO](https://releases.ubuntu.com/24.04/ubuntu-24.04.5-live-server-amd64.iso), [SHA256SUMS](https://releases.ubuntu.com/24.04/SHA256SUMS). SHA256: `97f3d7ffb032c3eb3b23d2c8be9cc76e60c2c1f2c0146ba5ba9fe01cafae0fd8`.
- HTTPS metadata GET/HEAD로 hash·용량만 확인했고 ISO는 받지 않았다. 스크립트는 이 hash·용량을 고정 검증한다. [Ubuntu 권장 GPG 서명 검증](https://documentation.ubuntu.com/security/software-integrity/image-verification/)은 아직 미실행이다. 이미지 진위·보호 runtime PASS로 확대하지 않는다.
