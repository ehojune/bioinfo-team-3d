## 단계
#1200 후속: [PR #1201](https://github.com/ehojune/bioinfo-team-3d/pull/1201) 리뷰 P2(AlphaGenome 표시).

## 바뀐 것
- `labhq/orchestrator/cso.py`: `format_roster(agents, *, alphagenome=True)`와 `_annot_roster(a, alphagenome)`. 허용 목록이 있는 직원에서 AlphaGenome을 쓸 수 없으면 `alphagenome`·`alphagenome_variant`를 목록에서 빼고, 남는 도구가 없으면 `labhq_annot` 항목을 적지 않는다. 허용 목록이 없는 직원은 이미 "AlphaGenome when its key is set"이라고 적으므로 그대로.
- `Orchestrator._alphagenome_ready()`: 키 파일(`annot_keys.read_key`)과 클라이언트 설치(`alphagenome_client_available`)가 모두 있을 때만 참. 계획·재계획 prompt를 만드는 세 곳(연구 계획, 일반 계획, 재계획)이 이 값을 넘긴다.

## 시험
- `tests/test_annot_literature.py`에 4건 추가: 사용할 수 있을 때와 없을 때의 명단, 남는 도구가 없을 때 항목 생략, 허용 목록이 없는 직원 그대로, `_alphagenome_ready`의 키·클라이언트 조합 4가지. 먼저 추가해 수정 전 실패를 확인했다. 관련 `test_annot_literature.py`·`test_cso.py`·`test_ask_policy.py`·`test_branched_research.py`·`test_research_protocol.py` 406 passed.

## 하지 않은 것
- 게이트웨이와 runner가 다른 컴퓨터일 때 게이트웨이의 키 파일 유무가 runner와 다를 수 있다. 이 판단은 게이트웨이의 설정 기준이다.
- Atlas 도구(`alphagenome_atlas`)는 허용 목록 이름이 아니라 이번 범위가 아니다.
