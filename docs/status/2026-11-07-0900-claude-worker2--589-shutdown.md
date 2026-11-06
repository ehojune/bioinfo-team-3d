## 단계
#589 rev2: PI 인스턴스 종료 기록 감사 문서와 supervisor heartbeat 간격 개선.

## 한 일
- `docs/reference/pi-instance-shutdown-589.md`: 10-24 이후 PI 인스턴스는 돌지 않아 자료가 없고(마지막 시작 10-20 06:29), 10-20 09:59~10:09에 gateway·runner·두 supervisor가 종료 기록 없이 함께 사라졌음을 diagnostics의 pid별 시간표로 보였다. 원인 후보 다섯과 남은 일.
- `labhq/supervisor.py`: `HEARTBEAT_S = 600`을 두고 supervisor의 `ProcessDiagnostics`에 넘긴다(전에는 3600). gateway·runner의 기본 간격과 같다.
- `tests/test_supervisor.py`: 상수가 `ProcessDiagnostics`의 기본 간격과 같은지, `main`이 그 상수를 넘기는지 시험 2건.
- 로그 증가: heartbeat 한 줄 약 150바이트. supervisor 둘 합쳐 하루 48줄(약 7 KiB)에서 288줄(약 42 KiB)로.

## 확인
- `tests/test_supervisor.py`와 diagnostics 시험 37 passed(Windows 로컬). 줄 크기는 실제 PI 인스턴스 supervisor 로그의 heartbeat 242줄 평균이다.

## 하지 않은 것
- PI 인스턴스는 건드리지 않았다. 프로세스 생성·종료 감사 켜기(PI)와 인스턴스를 며칠 돌려 보는 일은 #589를 열어 둔 채 남아 있다.
