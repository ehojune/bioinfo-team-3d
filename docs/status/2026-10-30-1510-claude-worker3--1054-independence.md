## 단계
#1054(② 5차 s3 실패): "같은 출처 pmid:41820335를 다른 independence group으로 인용해 단계 계약 전체가 무효"로 알려진 문제.

## 원인(재현으로 확인)
- 같은 출처·다른 group 하나만으로는 단계가 실패하지 않는다. 교정 뒤 `salvage_research_result`가 뒤 행만 거부한다(재현 시험 `test_recited_source_alone_refuses_only_the_second_row`).
- 5차 s3의 실제 오류는 둘이 한 목록에 있었다: 재인용 group 불일치(살릴 수 있음) + `claim c_mechanistic_hypotheses is outside the claim_ids [...] that step declared`(단계 binding이라 살리지 않음). 후자 때문에 salvage가 None을 돌려 단계 전체가 실패했고 s4~s6이 건너뛰어졌다(근거: `D:/.labhq/dev/tmp/claude-worker2/step2/card-cp2-5.json` warnings[0]).

## 한 일
1. `labhq/research/contract.py` `salvage_research_result`: 단계가 선언한 `claim_ids` 밖의 claim은 그 claim과 링크만 거부(`refused`·`unsupported`에 올려 CP2에 표시)하고 나머지로 계속한다. 단계 claim이 하나도 남지 않으면 그대로 실패한다. 필수 evidence 칸·plan hash 같은 binding은 여전히 살리지 않는다.
2. 재인용 group은 합치지 않고 뒤 행 거부를 유지한다. 근거: 합치면 직원이 잘못 적은 독립성이 보이지 않고, 거부는 CP2에 "re-citation is not independent"로 남아 독립 근거를 부풀려 세지 않는다(연구 규약의 과대계산 방지). `labhq/evidence`의 검사는 건드리지 않았다.
3. `tests/test_contract_slot_repair.py` 5건(재인용만, 재인용+범위 밖 claim, 범위 밖 claim만, 단계 claim 전무는 실패 유지, 선언 안 한 evidence slot은 계속 실패), manual 한·영.

## 테스트 결과
- Windows `test_contract_slot_repair`·`test_research_cp2`·`test_research_evidence` 244 passed.
- 미검증: 실제 ② 요청에서의 재현, 범위 밖 claim을 만든 직원 프롬프트 쪽 원인(왜 claim_ids 밖 claim을 냈는지)은 보지 않았다.
