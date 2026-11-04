## 결론

PR #554: pytest가 사용자 gateway에 요청을 남기지 않도록 홈·state를 격리했다. 정리 도구는 실제 store에서 실행하지 않았다.

## 바뀐 것

- 수집 전·test별 HOME/USERPROFILE/AppData/XDG 격리와 사용자 `.labhq` 접근 guard. subprocess에도 적용.
- `test_direct_outputs.py`의 gateway tmp 누락 수정. 현재 demo test의 tmp 경로 확인 추가.
- `scripts/purge_test_requests.py`: dry-run 기본. 정확한 문구·agent·mode가 일치한 요청과 이벤트만 선택하며 apply는 gateway 종료 확인과 새 SQLite 백업을 요구한다.

## 확인

- 합성 사용자 홈으로 재현한 guard 회귀: 수정 전 2 failed → 수정 후 통과.
- Windows 전체: **5,630 passed, 66 skipped**. 실제 Node 실행 파일을 테스트 프로세스 PATH에 넣어 사용자 AppData에 의존하는 래퍼를 피했다.
- 읽기 전용 SQLite 전후: 요청 **47→47**, test 문구 **38→38·4→4·4→4**. 증가 모두 0.
- mtime 변경 1건은 state 아래에 있었다. PI가 실행 중이므로 파일 무변경을 주장하지 않고 요청 건수로 판정했다.
- 공개 검사·Python 3.10 문법 검사 통과. CI 결과는 PR checks에 기록한다.

## 미해결

- 데모 문구 8건의 과거 유입 출처는 미확정. 현재 demo test는 tmp gateway를 사용한다.
- 검증된 basetemp 삭제는 자동 승인 검토의 `blocked by policy`로 차단돼 폴더가 남았다.
- 실제 purge·PI/trial 제어·설정 변경·리뷰 요청·병합은 하지 않았다.

Refs #545, #221

🤖 Generated with Codex (gpt-6) for the labhq dev lead
