## 단계
#1065(#1058 후속): producer가 혼자 막을 수 없던 하류 staging 한도 셋.

## 한 일
1. **누적 입력 예산(`step_inputs.stage_inputs`):** 상류 하나의 필요량이 단계 합계의 남은 양을 넘으면 복사 전에 `cumulative input budget: needs N bytes, M bytes are left of the T bytes ...`로 그 상류만 거절한다. 거절된 상류가 복사하며 쓴 byte는 합계에 돌려준다(이전에는 복사 도중 거절되면 쓴 byte가 합계에서 빠져 뒤 상류가 불필요하게 거절됐다). 상류는 일부만 복사하지 않고 전부 또는 없음을 유지한다.
2. **hard link:** `_copy`의 거절 사유를 hard link·파일당 한도·남은 합계로 갈랐다. producer(`daemon`)는 수집 파일 중 link가 둘 이상인 것을 `linked_files`로 찾아 `staging_problem(..., linked)`가 `a hard-linked file; a later step refuses it`로 단계를 incomplete 처리한다.
3. **스캔 범위:** `outputs/` 전체 스캔 note가 있어도 선언 폴더마다 `folder_files`로 그 폴더만 같은 상한(`reference_scan_max_entries`·`depth`)으로 걸어, 끝까지 못 걷거나 걸은 파일 중 스캔 기록에 없는 것이 있을 때만 그 폴더를 incomplete로 한다. 선언하지 않은 하위 폴더나 실행 전 스캔의 오래된 note는 더 이상 단계를 실패시키지 않는다.
4. 시험은 등록된 `tests/test_observed_outputs.py`에 7건(누적 예산 이름·byte 반환·hard link 하류/상류·`staging_problem` linked·무관한 하위 폴더). 수정 전 코드에서 4건이 실패함을 확인했다. manual 한·영.

## 테스트 결과
- Windows `test_observed_outputs`·`test_step_inputs_665`·`test_step_inputs_review_791`·`test_labhq_verify` 91 passed·3 skipped(POSIX 전용).
- 미검증: 실제 연구 요청의 다중 상류 staging, POSIX(WSL)에서의 hard link 시험(Windows NTFS에서만 돌림), 스캔 기록에 없는 파일을 판정하는 `folder_files` 깊이 의미가 스캐너와 정확히 같은지(같은 상한을 쓰나 깊이를 `outputs/` 아래 단계 수로 환산한 근사).

## CI 메모
- clean-install 한 건이 Windows 임시 폴더 정리의 WinError 32(VSApplicationInsights 파일 잠김)로 실패했다. 같은 run에서 doctor는 통과했고 코드와 무관한 기반 시설 flake라 기록 커밋으로 CI를 다시 돌린다.
