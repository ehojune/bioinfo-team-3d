## 단계
#660 1단계(`labhq talent` 카드 정보): 결정은 [#660 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/660#issuecomment-6528746993) 설계와 총괄 승인(공개 여부 칸은 만들지 않고 본문 포함 여부와 보관 위치를 적는다, 요약은 하지 않는다).

## 한 일
1. `paper_skills.skill_content(folder, digest)`: SKILL.md 앞부분(8 KB)의 이름·설명 한 줄, `references/paper.md` 크기, 그림·표·보충 그림·보충 표 파일 수, 보충자료 파일 유무를 읽는다. 링크나 다른 hash의 폴더, 손상된 파일은 None 또는 이름 없음으로 끝나며 아무것도 따라가지 않는다.
2. `cli.talent_lines`: 같은 줄 끝에 `이름=… · 논문 본문 파일 N KB(이 PC의 이 폴더에만 있고 요청 묶음에는 들어가지 않음) · 그림·표 수 · 보충자료 있음 · 기록 시각(파일) YYYY-MM-DD`. 줄 수는 그대로(기존 시험이 고정)이고 경로는 보이지 않는다.

## 이 PC에서의 실제 출력
스킬 3개(receipt 있는 것)에 본문 파일 154 KB·3 KB·49 KB, 그림 10·0·4 등이 나온다. 3 KB짜리는 본문이 매우 짧아 초록 수준일 수 있다(크기로 드러남).

## 추가한 것 (rev3)
- **만든 요청·날짜(`skill_origin`)**: `paper_skills.skill_origins`가 요청들의 `plan.paper_skill_results`에서 이 `skill_sha256`이 처음 `ready`로 나온 요청(id·`created_at`)을 찾는다(`reused`는 이전 요청이 만들었다는 뜻이라 세지 않는다). `labhq talent`는 로컬 gateway store(`gateway.sqlite3`)를 **읽기 전용**(`mode=ro`)으로 열어 읽는다. 없으면 `기록 시각(파일)`로 대신하고 괄호에 이유(`요청 기록에 없음`, `gateway 기록 파일(gateway.sqlite3)이 없습니다`, `요청 기록을 읽지 못했습니다(오류 종류)`)를 적는다. store를 만들거나 쓰지 않는다.
- **끝나지 않은 빌드**: `build/`만 있고 receipt가 없는 `paper-skill-*` 폴더 수를 마지막 한 줄(`빌드 미완료 폴더 N개`)로 알린다. `tests/test_contract_provenance.py`의 줄 수 단언을 약하게 하지 않고 새 줄을 고정했다.
- 이 PC에서 실제 store로 돌린 결과: 스킬 3개 중 2개는 `req_750b0e41fd`가 만든 것으로 나오고, 하나는 요청 기록에 없다(빌드한 요청이 store에 없거나 필드가 없던 시절의 요청).

## 하지 않은 것
- 요청 사이 사용 이력(쓴 요청·단계·직원 누적)과 웹 카드(2단계, gateway API).
- (manual 한·영은 rev4에서 `labhq talent` 절에 새 출력 항목을 적었다.)

## 테스트
`tests/test_paper_skill_670.py`에 시험 4건(내용 읽기, 링크·잘못된 hash·큰 파일, talent 줄 내용과 경로 비노출, 읽을 수 없을 때 이전 줄+날짜). 수정을 빼면 4건 모두 실패한다. `test_paper_skill_670`·`test_paper_skill_attach`·`test_contract_provenance` 112 passed.
