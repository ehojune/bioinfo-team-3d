## 단계
Windows CI 실제 R parse 시험의 시간 초과 — Refs #898 (닫지 않음).

## 근거 (실제 로그)
- run 40022968041(main `481221f8`) job 120652090937: `test_real_r_parse_without_running_the_script[stop('not evaluated')\n-pass]` 실패, `assert 'timeout' == 'pass'`.
- 단계 기록: `[{"phase":"version","timeout_s":10,"returncode":0,"elapsed_s":7.704},{"phase":"syntax","timeout_s":10,"timeout":true,"elapsed_s":10.0}]`, 전체 call 17.72초. version이 7.704초 걸려 성공한 뒤 syntax가 10초 한도를 넘겼습니다. 10-26 #987 조사(run 40305182030)도 version 성공 뒤 syntax 초과로 같은 형태입니다.
- 이 두 시험이 확인하는 것은 실제 R이 script를 실행하지 않고 parse하는지입니다. Rscript 시작이 늦은 것은 판정 대상이 아닙니다. R 시작이 느린 이유는 확인하지 못했습니다.

## 한 일 (`tests/test_reproduction_receipt.py`만)
- 실제 Rscript를 띄우는 `test_real_r_parse_without_running_the_script` 2건에만 `build_receipt(timeout=60)`(호출당)을 넘깁니다. 제품 기본값 10초와, 이를 `assert kwargs["timeout"] == 10`으로 확인하는 mock timeout 귀속 시험은 그대로입니다.
- `r_parser_trace` fixture가 version·syntax 소요 시간을 통과할 때도 `UserWarning`으로 남깁니다. CI 로그는 통과한 시험의 출력과 병렬 worker의 출력을 보여 주지 않지만 warnings summary는 출력하므로, 이 줄로 R 시작 시간 분포를 모을 수 있습니다. 기존 `print`·`record_property`는 유지합니다.

## 테스트 결과
- 로컬에는 `Rscript`가 없어 실제 R 시험 2건은 skip됩니다. 같은 파일 107 passed, 3 skipped. mock 시험으로 경고 출력은 확인했습니다.
- 미검증: Windows CI에서 실제 R 시험 2건이 skip이 아니라 실행돼 통과하는지, 60초가 충분한지. 이 PR의 Windows job 로그로만 확인할 수 있습니다.

## 남은 것
- 제품 기본값 10초가 실제 Windows 사용자 PC에서 모자랄 수 있는지는 이 PR이 답하지 않습니다. 모은 단계 시간으로 따로 판단합니다.
- SIGTERM ACK는 #900 이후 관측이 없지만 해결로 선언하지 않습니다. #898은 열어 둡니다.

## 바꾼 파일
tests/test_reproduction_receipt.py와 변경 기록.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
