# #798 논문 스킬 지연 생성 계약

- CP1 준비는 제공된 citation/access metadata만 읽는다. 네트워크·본문·PDF·변환을 실행하지 않는다.
- 계획의 `deferred` 행과 `selection_sha256`은 논문·담당 단계·선정 이유를 고정한다. 미래 스킬 내용까지 고정했다는 뜻은 아니다.
- 승인된 단계 dispatch에서 기존 예산·retry·취소 경계를 거쳐 변환한다. 승인/선정 변경은 실행을 중단하고 재승인을 요구한다.
- 생성 결과는 계획 대신 기존 단계 provenance에 남긴다. 내용 증거가 없는 지연 생성 단계와 그 종속 단계는 CP1 재사용 후보에서 제외한다. 독립 단계와 기존 hash 고정 receipt의 #807 검증은 유지한다.
- Runner가 정규직·요청·단계·러너·원본/사본 hash를 검사하고 owned `inputs/paper_skills/<skill_id>.md`를 주입한다. 사본은 실행 뒤 제거한다.
- 일반 DAG도 policy 구성 시 길이·내용 hash·무링크 handle로 재검사한 문서 파일만 기존 읽기 grant에 넣는다. `inputs/` 보호·조상 권한·native 쓰기 제한은 유지하고 조상 namespace가 겹치면 장착을 생략한다. 파일 불변을 보장하는 것은 아니며 결과 귀속 때 hash를 다시 검사한다.
- 결과의 `[paper-skill:<id>:<folder_sha256>:<document_sha256>]` 표기와 유지된 사본이 일치하고 단계가 성공해야 `called=true`다. 결과의 출처 표기이며 실제 tool 호출이나 과학적 성능 향상을 뜻하지 않는다.
- `false`는 확인된 참조가 없다는 뜻이다. `usage[].reason`은 `no_result_reference`, `input_changed`, `step_failed`, build/장착 실패를 구분한다. 구형 receipt나 runner 증거 부족은 `unverified`, 미실행은 `not_dispatched`다.
- 검증: 합성 metadata·파일과 fake adapter/Hub. [#821](https://github.com/ehojune/bioinfo-team-3d/pull/821) 병합 시 Linux 3.10·3.12·Windows CI가 통과했다. 실제 PDF·네트워크·연구·성능은 미확인이다.

## CI 계약 보정

- 두 prompt snapshot은 `labhq prepares them before CP1.`을 metadata 확인·승인된 단계 dispatch 문장으로 바꾼 차이만 반영한다. 문장을 되돌리면 이전 digest와 일치한다는 assertion도 유지하며 schema·output_types off/on 계약은 바꾸지 않는다.
- paper 중단 검사는 metadata 준비에서 callback 미호출을 확인한 뒤 승인 hash를 붙인 실제 CSO step 경계로 이동했다. fake dispatch의 runner shutdown과 asyncio cancellation이 모두 전파되고 연구 단계는 dispatch되지 않으며 계획·승인 hash는 유지된다. 제품 예외 처리는 이미 이 계약을 지켜 수정하지 않았다.
- 원 head의 네 실패를 mock으로 재현한 뒤 같은 네 node와 관련 17건이 통과했다. 로컬 timing은 각각 1.55초·4.06초이며 CI 성능이나 실제 process 중단의 검증은 아니다.

## 10-21 staff-path 검사 격리

- Linux 3.12 job 118674733476의 terminal error는 테스트의 전역 `Path` trap이 pytest traceback·cache·정리까지 차단한 것이다. 기존 staff-path 6조합은 단독 통과했고 무관한 진단 I/O를 끼운 mock 6건은 같은 메시지로 실패했다. 선행 E/F의 원 traceback은 로그에 없어 원인 전체를 확정하지 않는다.
- trap은 테스트 대상 staff/cache 경로로 한정하고 git fake는 해당 모듈에만 연결했다. 호출 내부 context에서 복원하므로 예상 밖 예외도 정상 보고한다. 실제 HOME/staff 사전 거부와 guard 전 I/O 금지는 유지하며 제품 코드는 바꾸지 않았다.
- 당시 로컬 검사 16건은 정상 진단·실패 시 복원·조기 staff/cache 읽기·git 차단을 확인했고 관련 HOME guard 3건도 통과했다. 실제 설치/git·process 실행은 수행하지 않았다.

## 10-22 `labhq talent`의 논문 스킬 목록 (#660)

- 빌드 성공 시 `talent/paper-skill-<key>/paper-skill.json`만 남고 `contract.yaml`이 없어, contract만 읽던 `labhq talent`에는 나오지 않았다. 이제 계약 줄 뒤에 receipt마다 한 줄(폴더 이름·논문 ID·skill hash 앞 12자리·Paper2Agent commit 앞 7자리)을 출력한다. "생성됨"만 뜻하며 단계 사용·호출·효용은 요청 기록과 부록의 몫이다.
- `status=ready`·64자리 hash·정규화된 논문 ID가 아니거나 256KiB를 넘는 receipt, 폴더 key와 다른 논문을 가리키는 receipt는 값을 보이지 않고 한 줄로 표시한다. 같은 폴더의 contract나 같은 논문·hash의 계약은 한 번만 나온다. skill 경로·본문 receipt는 출력하지 않는다.
- 검증: tmp 합성 receipt로 계약+논문 스킬 동시 목록, 손상·불일치·대용량 처리, 중복, 호출 표시 없음, `labhq talent` 명령 출력 4건을 작성했다. 로컬에 pytest가 없어 새 테스트 본문만 in-process로 직접 실행해 4건 PASS를 확인했다. 전체 실행은 CI에 맡긴다. 실제 빌드·스킬 사용·효용은 미검증이다.
