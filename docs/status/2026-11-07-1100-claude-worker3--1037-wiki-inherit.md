## 단계
#1037 rev16: 세 번째 단위 — 노출 상속, 재시작·구형 runner 시험, 새 저장소 생성 직전 재확인. 공개 보호는 아직 **UNVERIFIED**.

## 한 일
- `wiki_exposure.inherited(parent)`: 부모에서 새 요청을 여는 두 경로(CP1 수정 요청 sync·async, 모델 거부 뒤 다시 시작 sync·async)가 부모 수준으로 시작한다. 부모가 possible이거나 기록이 없으면 새 요청도 막힌다. 첫 저장에 들어가 재시작해도 유지된다.
- 이어 묻기(follow-up)는 같은 요청 안에서 돌아 같은 기록을 쓰므로 따로 고칠 것이 없다.
- 시험: 상속(수정 요청 sync·async, 모델 거부), 재시작 뒤 기록 유지와 구형 runner(`wiki_tools` 키 없음)의 possible 승격, 새 저장소 생성 직전 재확인(없애면 실패하는 것을 확인).
- manual 12장 한영, 참고 문서 갱신.

## 하지 않은 것
- 증거 수준 검사·스키마 hash, 독립 출처 인증, wiki 본문 마스크, 결과 저장소 선택 카드 표시.
- request id가 없거나 알 수 없는 요청에 묶인 task는 기록할 곳이 없다(현재 그런 호출을 찾지 못했다).
- 실제 runner 프로세스 간 통합 시험은 없다. 구형 runner는 roster dict로 흉내냈다.
