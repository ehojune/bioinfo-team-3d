## 결론

PR #766의 [Windows 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39313316682/job/118268363866)를 test 커밋 `7e08044`로 보완했습니다. 제품 코드는 변경하지 않았습니다. 앞선 process-stop 수정·남은 범위는 [이전 기록](2026-10-20-0441-broad-process-stop-681.md)을 따릅니다.

## 변경·검증

- `test_facilities_ask_without_facilities_goes_to_the_cso_without_waiting`의 마지막 yield에서 중복 2초 timeout이 만료됐습니다. facilities 대기는 즉시 실패시키고 ask 작업의 기존 3초 제한과 답변·라우팅 검증은 유지합니다.
- 가상 시계로 같은 TimeoutError를 재현해 **1 failed → 1 passed**. 관련 facilities mock test는 **4 passed**. Git provenance 조회도 mock으로 격리했습니다.
- 최초 시도에서 Git 메타데이터용 subprocess를 accident guard가 1건 차단했습니다. 실제 실행 없이 경계를 mock으로 바꾼 뒤 추가 차단은 0건입니다. guard와 환경 변수 3개를 유지했고 전체 실패명·traceback은 로컬 artifact에 보존했습니다.
- Python 3.10 AST·diff·공개 검사 PASS. patch-note는 기존 flat entry에 기록합니다.

## 한계

실제 process/proc_tree integration·운영 실행은 하지 않았습니다. push 후 CI 대기·재리뷰·P1=0 판정·병합·배포는 supervisor가 맡습니다.
