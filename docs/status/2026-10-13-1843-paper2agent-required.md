## 결론

#549 D: Paper2Agent를 직원 설정에 설치하고 doctor가 설치·가시성·고정 commit을 검사한다. PR #553의 2차 지적 P1·P2를 수정했다.

## 바뀐 것

- Claude는 recruiter에만 plugin을 연결하고 Codex는 실제 recruiter 엔진과 직원 CODEX_HOME을 따른다. 상위 설정 폴더 보호를 유지한다.
- `94deac0`: 설정한 pin과 설치본의 `.labhq-source.json` commit이 다르면 doctor fail. `--fix`가 설정한 commit을 설치하고 재검사한다. pin이 없으면 기존 동작을 유지한다.
- recruiter YAML의 `allow_skills`를 제거하고 기존 Claude adapter 처리를 사용한다. 엔진을 codex로 바꿔도 roster를 읽는다.
- `3695d43`: 기존 plugin 테스트도 recruiter의 adapter 허용을 반영한다. 격리·plugin·skill 활성화 검사는 유지한다.

## 실행한 것

- `7b38dd9`에서 새 테스트 9건 중 7건 실패·2건 통과. 수정 후 관련 테스트 162 passed·3 skipped.
- Windows 전체 pytest 최종: **5648 passed·66 skipped**, 692 warnings. 첫 실행의 plugin 기대값 실패를 수정했고 semantics 기록 실패는 재실행에서 통과했다.
- 공개 검사·diff 검사 통과. 패치노트·목차 검증과 최종 head CI는 PR checks에서 확인한다.

## 남은 지적

- 이번 pytest 임시 폴더 삭제는 실행 정책이 차단했다.
- 실제 직원 CLI·PI·trial은 실행하지 않았다. 임시 홈과 모의 설치로 검증했다.

Refs #549

🤖 Generated with Codex (model slug unavailable) for the labhq dev lead
