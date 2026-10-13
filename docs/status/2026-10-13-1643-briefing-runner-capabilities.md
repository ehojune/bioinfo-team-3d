# runner capability 브리핑

- 권고: 브리핑이 계획과 같은 정제 요약을 받아 현재 계산 자원의 한계부터 적는다.
- PR: [#552](https://github.com/ehojune/bioinfo-team-3d/pull/552), branch: `briefing-runner-capabilities`.

## 변경

- `scheduler=none`·`labhq_hpc=no`·도구 `no`는 사용 불가, `unknown`·보고 부재는 미확인으로 안내한다.
- raw capability manifest 대신 기존 `format_capabilities` 결과만 브리핑에 넣는다. 별도 doctor·원격 조회·모델 호출은 없다.
- 비서실장이 없으면 기존 계획 경로를 그대로 탄다.

## 검증

- 새 회귀 2건: 기준 코드에서 2 failed, branch에서 2 passed.
- `tests/test_cso.py`: 203 passed.
- Windows 전체 pytest: 5619 passed, 66 skipped.
- 공개 검사와 diff 검사 통과. 전용 TEMP·state·basetemp는 검사 뒤 삭제했다.
- 개발 기준 `e219c68`; triage 기준 `70f5b72` 이후 관련 경로 선행 변경 `a428dba`·`1903c15`를 확인했다.

## 미해결

- 없음. CI·자동 리뷰 판정과 병합은 총괄에게 넘긴다.

Refs #36

🤖 Generated with Codex (gpt-5.6-sol) for the labhq dev lead
