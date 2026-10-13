# gateway 종료 진단

- 권고: 종료 원인은 미확인. 진단 장치를 배포한 뒤 다음 종료의 PID·시각·fault·exit를 맞춰 본다.
- PR: [#547](https://github.com/ehojune/bioinfo-team-3d/pull/547), branch: `gateway-exit-diagnostics`. 기존 인스턴스·설정·시스템은 변경하지 않았다.

## 조사 결과

읽은 자료: instance `gateway.log`·`runner.log`, 실행 wrapper와 trial 로그, Windows Application·System(10-12 18:00~10-13 09:30), 운영 보조 스크립트. 원본 이벤트와 로컬 경로는 공개 저장소에 넣지 않는다.

| 가능성 순 후보 | 근거와 한계 |
|---|---|
| 1. 외부 강제 종료 또는 Windows job 수명 영향(추측) | gateway 로그에 종료 문구가 없고 runner는 02:59:48 연결 단절을 기록. 운영 재시작 스크립트에는 명령줄을 골라 강제 종료하는 코드가 있지만 해당 시각 실행 증거는 없다. 자식은 detached/new process group이며 job breakaway는 지정하지 않는다. WMI 부모 영향도 확인되지 않았다. |
| 2. native crash(추측) | python.exe/combase.dll/c0000005 WER가 21:25·01:25·05:35~05:36에 있다. Application Error 1000은 없고 gateway PID·02:59와 연결되지 않는다. 다른 Python 또는 지연 보고일 가능성을 배제하지 못한다. |
| 3. Python 예외·SystemExit의 관측 누락(추측) | 자식 stdout/stderr는 이미 파일로 향한다. 다만 stdout은 버퍼링되고 별도 exit/fault 기록이 없어 마지막 줄만으로 예외를 배제하지 못한다. 확인된 예외는 없다. |
| 4. 메모리·handle 고갈(근거 부족) | 조사 시간대 Resource Exhaustion 기록이 없고 자원 추세도 없다. 다음 heartbeat에서 확인한다. |
| 5. 절전·재부팅·콘솔 정리(근거 부족) | 조사 시간대 Kernel-Power/Power-Troubleshooter 관련 기록이 없다. 02:50~03:10은 BITS 설정 변경·DCOM 10016·IsolatedUserMode·라이선스 서비스 이벤트뿐. trial 생존과 detached 실행도 PC 전체 정리를 뒷받침하지 않는다. |

`up` wrapper는 18:29:05 exit 0으로 끝났다. 이는 자식 gateway의 종료 코드가 아니다. 진단 공백을 고쳤으며 종료 원인을 고쳤다고 주장하지 않는다.

## 변경·검증

- gateway·runner에 faulthandler, sys.excepthook, asyncio 예외 handler, atexit, 지원 signal 기록과 heartbeat 추가. 기존 signal handler와 runner의 두 번 Ctrl+C 규칙을 보존한다.
- `up`은 기존 stdout/stderr 파일 전달을 유지하고 unbuffered·초기 faulthandler를 켠다.
- `status`·`doctor`는 PID 기록을 보존하며 프로세스 없음과 마지막 heartbeat·exit를 표시한다.
- 새 회귀: main `70f5b72`에서 4 failed. branch 관련 검사 107 passed·1 skipped.
- 첫 Windows 전체 검사는 5,538 passed·66 skipped·encoding 검사 1 failed. encoding·바이너리 모드를 고친 뒤 관련 11 passed. 전체 재검사와 CI 결과는 PR 검증 절에 기록한다.
- 다음 종료 때 볼 파일: `gateway.state_dir/logs/{gateway,runner}-diagnostics.jsonl`, `{gateway,runner}-fault.log`, `{gateway,runner}.log`. 상세 사용법은 [manual](../manual.md).

남은 지적: 외부 강제 종료는 프로세스 내부 hook으로 종료 코드를 얻지 못한다. 이번 작업은 자동 재시작·상주 감시자를 추가하지 않는다.
