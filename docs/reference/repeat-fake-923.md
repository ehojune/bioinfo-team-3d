# 같은 fake 요청 2회 검사 (#923)

조회용 검증 설명입니다. 실제 모델 연구·과금·HPC·hard-stop 증거는 아닙니다.

```powershell
python scripts/repeat_fake_check.py --source <committed-checkout> --output <new-empty-folder>
```

- #922 `clean_install_check.py --work-dir … --receipt …`를 그대로 재사용합니다. 그 설치 smoke 1회가 끝난 뒤 동일 설치판에서 비교 대상 요청 2회를 실행합니다.
- `installation.json`, `run-{1,2}/receipt.json`, `request.json`, `tasks.json`, SQLite state, 산출 묶음, `comparison.json`을 보존합니다. stdout/stderr는 `logs/`에 따로 씁니다. 실패는 exit 1입니다.
- 두 run은 각각 빈 state/workspace와 새 process를 씁니다. 실제 gateway HTTP → runner WebSocket → mock adapter → report/bundle 경로입니다.
- mock에는 process 시작점이 없으므로 fixture가 `ctx.before_spawn()`을 호출합니다. 실제 runner가 baseline과 hash를 기록하며 검사기가 결과 hash를 주입하지 않습니다.
- OS·Git metadata는 시작 전에 cache하고 host package probe는 생략합니다. Audit은 service 시작부터 종료까지 적용합니다.

| 같아야 하는 항목 | 각 run 검사 |
|---|---|
| 종료·비용 | `done`, bundle `complete`, 모든 task 성공·알려진 비용 $0, request $1/task $0.05 설정 |
| 시간·네트워크 | 요청 120초 이내, task timeout 120초, lookup cap 0. Audit hook이 외부 socket·자식 명령을 거부하며 시도 0건을 요구 |
| 산출·근거 | 동일 파일 구조·artifact bytes/hash, runner provenance, 실행기록의 산출 경로, bundle 복사본과 MANIFEST hash, mock 표식 |
| 새 실행 | request ID·task ID 집합이 겹치지 않음, artifact file identity가 다름, task/run 시각이 이번 request 이후 |
| 종료 뒤 | 자식 process exit 0, 설치 venv의 잔류 Python process 0 |

시각·소요시간·ID·상대 실행 경로·file identity는 달라도 됩니다. 상태·비용·근거·파일 구조는 정규화하지 않습니다. artifact 줄바꿈은 실행 OS의 mock 원본 bytes를 그대로 검증하므로 Windows/Linux 간 byte 동일성을 주장하지 않습니다.

`repeat_fake_fixture.py`의 `REQUEST_BODY`, `configure(settings, root)`, `run_request(base_url, token, timeout_s)`, `collect_receipt(...)`를 #924에서도 쓸 수 있습니다. `exercise()`는 audit hook이 유지되므로 전용 자식 process에서만 호출합니다. 실제 provider billing·외부 조회·독립 hard-stop 집행·연구 품질은 미검증입니다.
