## #660 — 여우 선행 연구 목록과 논문별 자문 스킬 카드 (base 9d4836c)

- 변경: `paper_skills.literature_view`가 저장된 요청의 `analysis_precedents`·`paper_skill_results`·`recruit_results`·단계 receipt를 묶는다. 이를 `GET /api/requests/{id}`가 `paper_literature`로 함께 돌려준다. 새 route, 파일 읽기, 빌드, 다운로드는 없다. 2.5D 여우 시트(`ui/literature.js`)는 논문마다 쓰임(계획 참고·자문 스킬·재현 채용)을 보이고, 행을 누르면 스킬 카드가 열린다. 표시는 text만 쓰고, 링크는 확인한 DOI/PMID로만 만든다.
- 결과 참조(`called`)는 도구 호출이나 효용의 증거가 아니라고 표시한다. 실행 기록의 스킬 줄도 "호출됨" 대신 "결과 참조: 확인/미확인/실행 전"으로 바꿨다.
- 손상 기록은 스킬 표시를 생략한다(`damaged`). 제목은 300자로 자르고 제어문자를 지운다. 본문·talent 경로는 내보내지 않는다.
- 로컬: 새 serializer 테스트 2건을 in-process로 직접 실행해 통과했다. 변경 JS는 `node --check`를 통과했다. API·Node renderer 테스트는 CI에서 확인한다(로컬에 fastapi·pytest가 없고 JS 실행은 금지).
- 미실행·남은 것: 3D 화면 연결, 실제 요청에서의 부착·결과 참조·효용, 실제 브라우저 표시. manual 갱신은 별도 patch로 총괄에 넘긴다. #660은 닫지 않는다.
