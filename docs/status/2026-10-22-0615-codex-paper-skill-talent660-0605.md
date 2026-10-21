## #660 — `labhq talent`에 빌드된 논문 자문 스킬 표시 (base c16dcdb)

- 변경: `labhq/cli.py`의 `talent_lines`가 기존 contract 줄 뒤에 `paper-skill.json` receipt를 한 줄씩 출력한다. "생성됨"만 뜻하며 사용·효용은 요청 기록에서 본다. 손상·폴더 불일치·대용량 receipt는 값 없이 표시하고 중복은 한 번만 나온다. [참고](../reference/paper-skills-798.md#10-22-labhq-talent의-논문-스킬-목록-660).
- 로컬: 새 테스트 4건의 본문을 in-process로 직접 실행해 PASS를 확인했다(pytest 미설치, 무거운 test 모듈 import는 `markdown_it` 미설치로 불가해 helper만 잘라 실행). Python 3.10 AST, `check_public.sh`, `git diff --check`를 통과했다.
- 미실행: pytest 전체, 실제 Paper2Skill 빌드·스킬 사용·효용, 웹 UI. #660의 선행문헌 목록 UI와 실요청 효용 확인이 남아 있어 닫지 않는다.
- 통합: 승인 main `99f1c49`(#840 `18b4fbd` 후손, 이후는 생성 목차만)를 merge `37f7ce0`로 명시 통합했다. 양쪽 patch-id와 merge-tree가 일치하고 `cli.py`·새 테스트 blob은 그대로다. #840의 직원 env 수정도 그대로 들어 있다.
