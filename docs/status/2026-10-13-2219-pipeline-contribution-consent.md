새 설정의 `labhq init`에서 pipeline 코드를 공개 upstream에 PR로 기여할지 묻습니다. `y`(대소문자 무관)만 동의이며 Enter·`--yes`·불분명한 답은 끔으로 저장합니다.

- 기존 동의값과 upstream 대상은 `--force`에서도 보존합니다.
- dry-run은 설정·token·직원 홈을 만들지 않으며 새 instance마다 동의를 따로 받습니다.
- 거절 시 내용 수집·GitHub 호출을 막는 기존 경로를 검증했습니다. fork 자동 생성은 후속 작업입니다.
- 설치와 bioinfo-agent 연결 매뉴얼을 갱신했습니다.

검증: 기준 main `2ffb06d`에서 새 회귀 10 failed·3 passed → 수정 후 관련 129 passed. Windows 전체는 6,147 passed·66 skipped·45 failed: 격리된 홈에서 Node shim이 실행 파일을 찾지 못한 웹 테스트 45건입니다. 테스트 PATH에 Node 실행 파일을 직접 지정하자 해당 45건이 모두 통과했습니다. 보정 후 전체 재실행은 다른 레인의 전체 suite 때문에 미실행이며 로컬 전체 통과 조건은 아직 충족하지 않았습니다.

공개·diff·패치노트·목차 검사 통과. 코드 head `bbb5dd9`의 CI는 pytest 3.10·3.12·Windows·slow, clean-install, ro-crate-validator, changes 성공·readme skipped였습니다. 누락 기록을 넣은 `428dc3c`는 changes·patch-notes 성공이며 나머지는 기록 전용 변경으로 skipped입니다. 생략된 job을 실행 통과로 세지 않습니다.

Refs #300

남은 지적: 보정한 테스트 환경에서 Windows 전체 재실행이 필요합니다. fork 지원은 A의 main 반영 뒤 B 레인에서 진행합니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
