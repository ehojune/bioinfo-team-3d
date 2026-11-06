# #1076 WMI `provider_timeout` 재발 3건 (2026-11-06)

lookup-only. 원인은 확정하지 못했다. 근거는 실패 run의 job 로그(`gh run view <id> --log-failed`, 재실행된 run은 `--attempt 1`)이고, 성공 run의 `WIN1029 launch stages` 줄을 기준선으로 썼다. 코드는 `labhq/windows_broker.py`, 단계 표지의 뜻은 [wmi-input-phase-825.md](wmi-input-phase-825.md), 집계는 [ci-1076-flake-tally.md](ci-1076-flake-tally.md)에 있다.

## 세 건

| | A | B | C |
|---|---|---|---|
| 시각(UTC) | 11-06 02:39 | 11-06 13:01 | 11-06 13:25 |
| run·job | [42151602313](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42151602313/job/127675999677), PR #1296 `30b77c01` | [42221743480](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42221743480) attempt 1 `clean-install`, PR #1313 `11d819d` | [42224884369](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42224884369/job/127915899306), main `3f018499` |
| 실패한 곳 | `test_windows_job_ci.py::test_allowed_breakaway_child_survives_parent_job_close` (Serial critical) | `clean_install_check.py`의 `up`: `Windows WMI bootstrap: provider_timeout` | A와 같은 시험 (Serial critical) |
| 도달한 표지(ms) | `powershell_started@0`, `stdin_open@16`, `header_read@4189` | `powershell_started@0` 뿐 | `powershell_started@0`, `stdin_open@26`, `header_read@9687` |
| 못 닿은 표지 | `input_read` 이후 전부 | `stdin_open` 이후 전부 | `input_read` 이후 전부 |
| Python이 잰 provider 시간 | **25 125 ms** | 15 062 ms | **24 094 ms** |
| provider stdout | 151 byte | 47 byte | 160 byte |
| 이후 | 새 head push 뒤 통과 | attempt 2 통과 | #1076 기록 대상 |

## 기준선: 성공한 run의 launch 두 번 (11-06 11:35–12:51 UTC, 5 run)

| run | 첫 launch `header_read` / 합계 | 둘째 launch `header_read` / 합계 |
|---|---|---|
| 42211413484 | 1029 ms / 2938 ms | 72 ms / 297 ms |
| 42213313775 | 1097 ms / 3265 ms | 107 ms / 438 ms |
| 42214492126 | 1093 ms / 3562 ms | 87 ms / 344 ms |
| 42219290467 | 2208 ms / 7391 ms | 86 ms / 344 ms |
| 42220837913 | 1075 ms / 3297 ms | 85 ms / 406 ms |
| 이 PC (로컬, 6회, create 직전까지) | 82–88 ms | 같음 |

로컬 측정은 `WMI_SCRIPT`를 `create_entered`에서 멈추게 잘라 같은 방식으로 돌린 것이다. WMI Create는 호출하지 않았다.

## 읽을 수 있는 것

- 셋 다 **`create_entered`에 닿지 않았다.** WMI `Create`는 호출되지 않았다. 이름은 `provider_create`지만 막힌 곳은 PowerShell 시작과 stdin 읽기 구간이다.
- 셋 다 **그 job(프로세스)의 첫 launch**였다. A·C는 이어진 launch가 `header_read` 105–114 ms로 지나갔고, B는 첫 `up`이다.
- 첫 launch는 성공할 때도 `header_read` 1.0–2.2 s, 합계 2.9–7.4 s로 둘째(72–107 ms)나 로컬(약 85 ms)보다 **12–25배 느리다.** 실패한 A(4.2 s)와 C(9.7 s)는 그 느린 쪽 꼬리로 보인다.
- 멈춘 자리가 건마다 다르다(B는 `stdin_open` 전, A·C는 `header_read` 뒤). 한 호출이 막힌 것이 아니라 **일정 시간 동안 PowerShell 전체가 느렸던 것**과 맞는다.
- A·C는 Python이 잰 시간이 `PROVIDER_TIMEOUT`(15 s)보다 9–10 s 길다. B는 0.06 s다. 이 초과분은 로그만으로 설명되지 않는다.
- C와 같은 job에서 13:26쯤 `web_resizable_panels`도 실패했다. node 모듈 진입에 19.3 s, Chrome `target-ready`에 17.6 s가 걸렸다([#1272](https://github.com/ehojune/bioinfo-team-3d/issues/1272)). 같은 러너에서 별개의 느려진 구간이 겹쳤다.

## 원인 후보

| 후보 | 근거 | 아직 모르는 것 |
|---|---|---|
| H1 첫 PowerShell·.NET 코드 경로의 cold start가 드물게 15 s를 넘는다 | 세 건 모두 첫 launch, 성공 run도 첫 launch가 12–25배 느림, 둘째 launch는 로컬과 같음 | 느린 것이 어셈블리 로드·JIT·백신 검사 중 무엇인지. CPU를 썼는지 기다렸는지 |
| H2 러너 전체가 일시적으로 느려진다(CPU·디스크 경합) | 멈춘 단계가 건마다 다름, C의 같은 job에서 Chrome·node도 17–19 s 정지 | 러너 구성·이웃 부하. B는 pip 설치 직후라 디스크 부하 가능성만 있음 |
| H3 Python 쪽 `subprocess.run` 흐름이 시간을 더 쓴다 | A·C의 +9–10 s(입력 쓰기, kill, 파이프 정리 중 어디인지 모름) | 시점별 Python 측 기록이 없다 |
| H4 WMI 저장소·`Win32_Process.Create` 자체가 느리다 | (반대 근거) 세 건 모두 Create 호출 전 | 해당 없음 |

## 고칠 범위(제안, 순서대로)

1. **진단 먼저.** 제품 동작은 바꾸지 않는다. `labhq/windows_broker.py::_create`가 Python 측 시각(Popen 반환, 입력 쓰기 끝, timeout 발화, kill 반환, 정리 끝)과 provider 프로세스의 CPU 시간(`GetProcessTimes`), 프로세스 안 launch 순번(첫/이후)을 diagnostics에 더한다. CPU 시간이 짧고 wall이 길면 H2, 길면 H1, 시각 사이가 벌어지면 H3을 가린다. 핸들이 필요해 `subprocess.run`을 `Popen`+`communicate`로 바꾸는 작은 변경이 든다. 시험은 `tests/test_windows_broker*.py`.
2. **CI 워밍업으로 H1을 시험한다.** `.github/workflows/test.yml`의 Windows job과 clean-install에서 pytest·`up` 전에 같은 스크립트를 `create_entered` 직전까지 한 번 돌린다. 이 뒤에도 첫 launch가 늦으면 H1은 틀린 것이다. 제품 코드는 건드리지 않는다.
3. **제품 변경은 위 데이터가 나온 뒤에 정한다.**
   - 재시도 한 번: `create_entered`에 닿기 전의 timeout은 `Create`가 호출되지 않았으므로 새 요청으로 다시 시도해도 안전하다는 근거가 있다. 다만 "단일 시도"(#825)와 요청 만료(`TIMEOUT` 20 s) 설계를 바꾸므로 총괄·PI 결정이 필요하다.
   - `PROVIDER_TIMEOUT` 상향은 권하지 않는다. B는 15 s 안에 `stdin_open`도 못 했고, 20 s 요청 만료와 맞물리며, 15 s는 #1029에서 "관측의 +50%"로 정한 값이다.
4. 하지 않는다: timeout만 늘리기, 시험 완화, `test_windows_job_ci`를 skip.

## 한계

- 오늘 3건만 셌다. #1076 댓글에 이미 기록된 것은 A·B뿐이고 C는 이 문서가 처음 정리한다. 분모(오늘 Windows job 수)와 이전 날짜의 빈도는 세지 않았다.
- 성공 기준선은 11-06 5개 run이다. 첫 launch 7.4 s가 한 번 있어 분포의 꼬리는 길다고 보지만, 표본이 작다.
- cold 상태를 로컬에서 재현하지 못했다. 이 PC는 warm이다(약 85 ms).
- 러너 이미지·백신·CPU 사양은 확인하지 않았다.
