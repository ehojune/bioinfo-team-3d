# #1225 — Atlas 시작 점검의 최종 정의를 manual에 적기

- 결론: manual 한·영의 AlphaGenome Atlas 절에 시작 점검 한 항목을 더했다. PR #1228(병합)에서 정한 최종 정의 그대로다. launch 설정에 키 값이나 환경 변수 이름 `ALPHAGENOME_API_KEY`가 어떤 모양으로든 있으면 실행을 시작 전에 거절하고, 예외는 설정된 키 파일 경로(`~` 펼침·절대·실제 경로)와 labhq가 그 경로로 만드는 비공개 경로 목록·Claude 규칙뿐이다.
- 바꾼 것: `docs/manual.md`, `docs/manual.en.md`. 코드·시험은 바꾸지 않았다. `integrations.py --check`가 통과한다.
- 확인: 문장의 근거는 `labhq/adapters/base.py`의 `names_atlas_key`·`atlas_key_file_forms`와 `tests/test_atlas_key_file_1225.py`(26건)다.
- 미검증: 이 절의 다른 문장(PR #1186이 "미병합·hold"로 적힌 제목 등)은 이번 범위가 아니어서 건드리지 않았고 현재 상태와 맞는지 확인하지 않았다.
