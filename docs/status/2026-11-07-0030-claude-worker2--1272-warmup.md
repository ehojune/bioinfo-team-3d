## 단계
#1272 rev6: Windows 직렬 단계 앞에 Node·PowerShell 워밍업을 둔다. 가설(첫 프로세스만 비쌈)을 검증하면서 그 비용을 시험 밖으로 옮긴다.

## 한 일
- `.github/workflows/test.yml`: `pytest-windows`에 단계 "Warm Node and PowerShell"을 "Warm Chrome" 앞에 더했다. `node -e 0`과 `powershell -NoProfile -NonInteractive -Command "exit 0"`을 두 번씩 불러 `ci warmup: {"node_cold_ms", "node_warm_ms", "powershell_cold_ms", "powershell_warm_ms"}`를 남긴다. 프로그램이 없으면 `null`이고, 2분 제한이다.
- 시간 한계, 시험, 재시도는 바꾸지 않았다.

## 확인
- 워크플로 YAML을 읽는 CI 시험 347건과 `-k "workflow or ci_ or yaml"` 1385건 통과, 새 단계의 스크립트를 로컬에서 직접 실행해 한 줄이 나오는 것을 확인했다(Windows 로컬).

## 하지 않은 것
- 실제 CI의 값은 아직 없다. 병합 뒤 몇 run에서 `cold`·`warm`과 resizable의 `node_start_ms`를 비교해 `docs/reference/ci-1076-flake-tally.md`에 적는다(그때 이 단계 설명도 함께 넣는다. 이 PR은 `.github/`만 바꿔 문서 갱신 규칙의 대상이 아니다).
