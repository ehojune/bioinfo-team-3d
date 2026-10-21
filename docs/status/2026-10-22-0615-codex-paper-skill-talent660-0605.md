## #660 — `labhq talent`에 빌드된 논문 자문 스킬 표시 (base c16dcdb)

- 변경: `labhq/cli.py`의 `talent_lines`가 기존 contract 줄 뒤에 `paper-skill.json` receipt를 한 줄씩 출력한다. "생성됨"만 뜻하며 사용·효용은 요청 기록에서 본다. 손상·폴더 불일치·대용량 receipt는 값 없이 표시하고 중복은 한 번만 나온다. [참고](../reference/paper-skills-798.md#10-22-labhq-talent의-논문-스킬-목록-660).
- 로컬: 새 테스트 4건의 본문을 in-process로 직접 실행해 PASS를 확인했다(pytest 미설치, 무거운 test 모듈 import는 `markdown_it` 미설치로 불가해 helper만 잘라 실행). Python 3.10 AST, `check_public.sh`, `git diff --check`를 통과했다.
- 미실행: pytest 전체, 실제 Paper2Skill 빌드·스킬 사용·효용, 웹 UI. #660의 선행문헌 목록 UI와 실요청 효용 확인이 남아 있어 닫지 않는다.
- 통합: 승인 main `99f1c49`(#840 `18b4fbd` 후손, 이후는 생성 목차만)를 merge `37f7ce0`로 명시 통합했다. 양쪽 patch-id와 merge-tree가 일치하고 `cli.py`·새 테스트 blob은 그대로다. #840의 직원 env 수정도 그대로 들어 있다.
- 이전 CI: head `7217330` run 39677646617은 Linux PASS, Windows 실패 1건(`test_web_3d.py` focus_layout의 demo cleanup 미완료, Chrome stdio·websocket 미종료로 30초 초과)이었다. `labhq/cli.py`와 새 테스트와는 무관한 기존 정리 실패다.
- 2차 통합: 승인 main `e18af0e`(#843 `bf51c58` 후손, 이후는 생성 목차만)를 merge `98af4d2`로 명시 통합해 #843의 Windows 정리 helper를 받았다. 충돌은 없고 main 대비 차이는 이 branch의 6파일뿐이다. `cli.py`와 새 테스트 blob은 그대로다. 옛 Windows 실패의 해소는 새 CI로만 판정하고, #844(감독 조기 종료) 원인 미확정은 별도로 남는다.
