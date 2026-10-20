## 단계
PR #766 CI fixture 보정 — `6954ab6`, Refs #681.

## 한 일
- 지정 head의 artifact는 Windows `test_resume_waits_for_active_recruit_receipt[active-True-plan_only]` 실패였습니다. 안내된 limiter fixture는 이미 `98f70d9`에서 수정됐고 이번에도 세 phase가 통과했습니다.
- 원본 resume coroutine의 code를 그대로 실행하되 그 coroutine의 polling 시계만 가상으로 고정했습니다. 중간 상태 확인은 barrier로 동기화합니다. product·기존 assertion·16개 matrix·0.2초 기한·2초 실제 watchdog은 유지합니다.

## 테스트 결과
- `tests/test_resume_receipt_clock.py`와 `tests/test_external_wait.py::test_key_fulltext_automatic_phases_use_task_deadline`: **6 passed**. FakeHub의 재개·기한 만료·취소를 검증했습니다.
- Python 3.10 AST·기존 assertion/matrix/기한 보존 비교·public·patch-note·notes·diff 검사 PASS. guard 환경과 전체 로컬 실패 로그를 보존했습니다.

## 한계
- 원본 Hub fixture의 로컬 실행은 `subprocess.Popen` guard에서 1회 차단됐으며 재실행하지 않았습니다. 전체 Hub/Windows 통합 결과는 미검증입니다.
- 실제 process/proc_tree·PI·연구·HPC와 late-storage/PDF 테스트는 실행하지 않았습니다. 전체 CI·fresh-head 리뷰·병합·배포는 controller가 맡습니다.
