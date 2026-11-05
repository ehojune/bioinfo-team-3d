## #779 — 모델 거부 뒤 새 계획 요청의 meta 보존 (source dec9f80)

- 변경: `Hub._model_refusal_request`가 원 요청 meta(bench `case_id`, 임의 tag)를 새 요청에 복사하고 `revised_from`만 뺀다. `revised_from`은 CP1 수정 요청 찾기(`revised_request_of`)와 `result_storage`의 부모 연결이 읽는 CP1 계보 키다. 거부 재계획의 연결은 기존 top-level `model_refusal_from`이다. 다른 제어 키는 없다(`rounds`는 meta를 그대로 기록만 한다). meta는 `dict[str, str]`이고 `RequestIn`이 새 dict를 만든다.
- 유지: 새 요청 id와 독립 계보, 부모 요청과 meta 불변, CP1 찾기 결과, 거부·그대로 종료·취소·예산 처리, 사용자 매뉴얼.
- 로컬(순수): 새 순수 검사 본문을 `RequestIn`과 두 Hub 메서드의 AST 추출로 in-process 실행했다. HEAD `99f1c49`에서 FAIL, 수정 후 PASS였다. fastapi가 없어 승인 route 검사와 파일 전체는 CI에 맡긴다. Python 3.10 AST/compile, `git diff --check`, `check_public.sh`는 통과했다.
- 범위 밖: #780(continuation 거부 뒤 카드)은 선택이 이전 round 없는 새 연구가 되는 설계 문제라 바꾸지 않았다.
