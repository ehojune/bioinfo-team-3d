## 결론

#560의 15번: 2.5D 메신저에서 위로 스크롤하면 저장된 첫 기록까지 조회합니다.

## 바뀐 것

- PI 전용 `GET /api/messenger`: 전체·요청별 조회, 이전 방향 seq 커서, 기본 40·최대 200건.
- SQLite `messenger_events`에 질문·답·직원 메시지·보고 알림을 보관합니다. 재전송 버퍼와 직원 log/feed 상한은 유지합니다.
- 불러오는 중·재시도·처음 표시, 페이지 중복 제거, 스크롤 위치 유지. 3D는 변경하지 않았습니다.
- `docs/manual.md`의 웹 사무실·API 설명을 갱신했습니다.

## 확인

- 새 회귀 테스트: origin/main에서 Python 4건·Node 3건 실패, branch에서 통과.
- 관련 Python 73건, Node 전체 44개 파일 통과. Windows 전체 pytest: 실행 대기(동시에 시작한 다른 레인의 전체 검사와 겹쳐 이 실행을 1%에서 중단, 통과 판정 아님).
- Chrome 합성 기록 125건: 40→80→120→125건, 첫 메시지·끝 표시·새로고침 후 재조회 확인.
- 공개 검사·diff 검사 통과. 패치노트·목차 검사는 기록 커밋에서 확인합니다.

## CSO가 보는 범위

메신저 전체가 자동으로 전달되지는 않습니다. 직원 질문은 질문·시도·선택지·참조와 해당 요청(4,000자)·계획(6,000자), PI 이어 묻기는 해당 요청·보고서(6,000자)·최근 완료 답변 3건을 받으며 재개한 해당 직원 세션의 맥락도 사용합니다.
근거: `labhq/orchestrator/cso.py`의 `answer_ask`, `run_followup`; 질문·답 저장은 gateway SQLite `state(kind='ask')`, PI 이어 묻기는 `state(kind='request').followups`입니다.

## 남은 지적

- 업그레이드 전에 이벤트 상한으로 이미 삭제된 메시지는 복구되지 않습니다. 기존 DB에는 남아 있는 이벤트만 처음 한 번 옮깁니다.
- 봇 판단과 병합은 개발 총괄에게 넘깁니다.

Refs #560

🤖 Generated with Codex (gpt-6) for the labhq dev lead
