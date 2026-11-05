# #578 rev7 Windows 실패 증거 보존

**hold 유지·미병합.** 실제 HPC는 실행하지 않았다.

| 변경 | 범위 |
|---|---|
| 실패 증거 | Windows report 뒤 console에 run/attempt와 checkpoint·stack·실패 진단·lock 상태를 최대 48 KiB 남긴다. 잠긴 파일의 상대경로·오류 종류·errno를 기록한다 |
| 업로드 | 원본에서 읽을 수 있는 파일만 별도 snapshot으로 복사한다. lock·임시 파일을 제외하며 파일당 32 MiB·전체 256 MiB·열거 512개로 제한한다. 초과·읽기 실패는 진단 manifest에 남긴다 |
| lock 관측 | worker/node/PID와 waiting·acquired·releasing·released/acquire_failed를 worker별 JSON에 보존한다. 최근 32개 기록을 유지하며 관측 오류는 원래 실행을 바꾸지 않는다 |
| 계약 | 선택·assertion·lock 보호·상한·coverage/PASS receipt 발급 조건을 유지한다. 새 mock 시험만 inventory에 등록한다 |

로컬: 관련 mock·선택·phase 회귀 **218 passed/1 skipped**, source/AST profile·public·diff 검사 통과. 잠긴 입력 보존과 정상 proof 동일성·불완전 session 거부를 확인했다. 실제 Windows 파일 잠금과 기존 timeout 원인은 아직 UNVERIFIED다.

최종 head CI·실제 리뷰 결과는 완료 후 이 문서와 PR에 기록한다. 기존 원인 진단은 [rev6 근거](../reference/windows-timeout-578.md)에 보존했다.
