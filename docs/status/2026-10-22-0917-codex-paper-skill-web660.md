## #660 — 여우 선행 연구 목록과 논문별 자문 스킬 카드 (base 9d4836c)

- 변경: `paper_skills.literature_view`가 저장된 요청의 `analysis_precedents`·`paper_skill_results`·`recruit_results`·단계 receipt를 묶는다. 이를 `GET /api/requests/{id}`가 `paper_literature`로 함께 돌려준다. 새 route, 파일 읽기, 빌드, 다운로드는 없다. 2.5D 여우 시트(`ui/literature.js`)는 논문마다 쓰임(계획 참고·자문 스킬·재현 채용)을 보이고, 행을 누르면 스킬 카드가 열린다. 표시는 text만 쓰고, 링크는 확인한 DOI/PMID로만 만든다.
- 결과 참조(`called`)는 도구 호출이나 효용의 증거가 아니라고 표시한다. 실행 기록의 스킬 줄도 "호출됨" 대신 "결과 참조: 확인/미확인/실행 전"으로 바꿨다.
- 손상 기록은 스킬 표시를 생략한다(`damaged`). 제목은 300자로 자르고 제어문자를 지운다. 본문·talent 경로는 내보내지 않는다.
- 로컬: 새 serializer 테스트 2건을 in-process로 직접 실행해 통과했다. 변경 JS는 `node --check`를 통과했다. API·Node renderer 테스트는 CI에서 확인한다(로컬에 fastapi·pytest가 없고 JS 실행은 금지).
- 통합: 승인 main `06dfb1b`(#847 `9588695`, doctor·manual 정정 포함)를 merge `3b5957a`로 명시 통합했다. 충돌은 없다.
- 3D: 기본 진입은 2.5D(`/`, `labhq open`)이고 3D는 `/3d`다. 3D 선택 패널에서 여우를 고르면 같은 `ui/literature.js`로 목록을 그린다. 데이터는 `live.js`가 기존 detail GET으로 요청 갱신마다 한 번 읽는다. `live.js`에는 새 import가 없다.
- manual: 선행 연구 절에 여우 목록·행 카드 설명을 추가했다. 0908 patch의 나머지 두 hunk는 #847로 이미 main에 있어 다시 적용하지 않았다.
- 독립 검토 P2 수정(`aadca75`): 목록이 아닌 `recruit_results`와 receipt의 목록형 status·reason이 `TypeError`로 요청 상세 GET 전체를 깨던 것을, 해당 입력만 좁게 검사해 `damaged`로 표시하게 했다. 같은 순수 fuzz 585건에서 수정 전 실패 경로 2개 → 수정 후 0. 기존 테스트에 serializer 1건·API 200과 저장 기록 불변 1건을 보탰다.
- 미실행·남은 것: 실제 요청에서의 부착·결과 참조·효용, 실제 브라우저 표시. #660은 닫지 않는다.
