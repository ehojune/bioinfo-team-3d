# #798 논문 스킬 지연 생성 계약

- CP1 준비는 제공된 citation/access metadata만 읽는다. 네트워크·본문·PDF·변환을 실행하지 않는다.
- 계획의 `deferred` 행과 `selection_sha256`은 논문·담당 단계·선정 이유를 고정한다. 미래 스킬 내용까지 고정했다는 뜻은 아니다.
- 승인된 단계 dispatch에서 기존 예산·retry·취소 경계를 거쳐 변환한다. 승인/선정 변경은 실행을 중단하고 재승인을 요구한다.
- 생성 결과는 계획 대신 기존 단계 provenance에 남긴다. 내용 증거가 없는 지연 생성 단계와 그 종속 단계는 CP1 재사용 후보에서 제외한다. 독립 단계와 기존 hash 고정 receipt의 #807 검증은 유지한다.
- Runner가 정규직·요청·단계·러너·원본/사본 hash를 검사하고 owned `inputs/paper_skills/<skill_id>.md`를 주입한다. 사본은 실행 뒤 제거한다.
- 일반 DAG도 policy 구성 시 길이·내용 hash·무링크 handle로 재검사한 문서 파일만 기존 읽기 grant에 넣는다. `inputs/` 보호·조상 권한·native 쓰기 제한은 유지하고 조상 namespace가 겹치면 장착을 생략한다. 파일 불변을 보장하는 것은 아니며 결과 귀속 때 hash를 다시 검사한다.
- 결과의 `[paper-skill:<id>:<folder_sha256>:<document_sha256>]` 표기와 유지된 사본이 일치하고 단계가 성공해야 `called=true`다. 결과의 출처 표기이며 실제 tool 호출이나 과학적 성능 향상을 뜻하지 않는다.
- `false`는 확인된 참조가 없다는 뜻이다. `usage[].reason`은 `no_result_reference`, `input_changed`, `step_failed`, build/장착 실패를 구분한다. 구형 receipt나 runner 증거 부족은 `unverified`, 미실행은 `not_dispatched`다.
- 검증 범위: 합성 metadata·파일과 fake adapter/Hub. PDF·네트워크·실제 연구·성능·process 실행은 확인하지 않았다. Windows/Python 3.10 전체 회귀는 CI가 담당한다.
