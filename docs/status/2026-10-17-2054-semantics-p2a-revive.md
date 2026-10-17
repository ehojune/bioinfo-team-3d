## 결론

semantics가 labhq 생성 ID를 private 식별자로 오인하던 경로와, citation 부가정보 때문에 P2A PMID를 읽지 못하던 경로를 고쳤습니다. Refs #559. Refs #660.

## 바뀐 것

- request·task·approval ID와 이름이 붙은 생성 hash는 경계 원문에서 빼되, private 경로·restricted 내용 차단은 유지합니다.
- `doctor`·`status`가 중단 이유·시각·경계·`labhq semantics enable`을 보입니다. 기존 `request_id:identifier` 오판은 자동 해제하지 않습니다.
- PMID 뒤 `access_level`이 붙어도 선택을 이어 갑니다. `full_text`만 빌드하고, 나머지는 계획·CP1·부록에 네 상태와 생략 이유를 남깁니다.

## 확인

- 새 회귀 test는 수정 전 5 failed, 수정 뒤 5 passed입니다.
- 관련 파일 24개: 998 passed, 2 skipped. 마지막 P2A 보정: 15 passed.
- `docs/manual.md`: 157,208자 → 157,209자.
