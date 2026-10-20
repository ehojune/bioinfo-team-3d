PR #766, 수정 `98f70d9`. [Python 3.10 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39328092592/job/118352072701)의 전체 traceback을 확인했습니다. 앞선 수정·한계는 [이전 기록](https://github.com/ehojune/bioinfo-team-3d/blob/e0512fd7b9c80bffe68d3ee4bf811e39880467cc/docs/status/2026-10-20-0628-broad-process-stop-681.md)을 따릅니다.

- operation timeout 뒤 progress task 정리 중 외부 1초 제한이 만료됐습니다. 해당 test의 5ms progress override를 제거해 제품 기본 60초 주기를 사용합니다. **80ms deadline·1초 외부 제한은 유지**하고 operation 취소·timeout 알림 assertion을 추가했습니다.
- 완료된 알림의 취소를 놓치는 Python 3.10 경로를 coroutine mock으로 모사해 **1 failed → 3 passed**. 실제 3.10 재실행은 controller 담당입니다. 로컬 Python 3.12에서 `tests/test_external_wait.py` **20 passed**, guard 환경 3개 유지·blocked 0건. 전체 실패명·traceback을 로컬 artifact에 보존했습니다.
- test가 이후 main의 #776에 추가돼 main `e0fa7e4`를 충돌 없이 통합했습니다. 자동 통합 tree와 비교한 직접 수정은 해당 test 하나뿐입니다. 제품 코드는 직접 수정하지 않았으며 late-storage/PDF test는 재실행하지 않았습니다.
- Python 3.10 AST·public·diff·patch-note·notes 검사 PASS.

[#786](https://github.com/ehojune/bioinfo-team-3d/issues/786)의 장기 정체와 같은 원인인지는 **UNVERIFIED**입니다. 실제 subprocess/proc_tree·PI·연구 실행은 미검증이며 CI·리뷰·병합·배포는 controller 담당입니다.
