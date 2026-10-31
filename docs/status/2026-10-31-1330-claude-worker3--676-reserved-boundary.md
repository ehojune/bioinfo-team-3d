## 단계
#676 후속: #1157 리뷰 P2 중 정상 계획을 거절하던 두 건.

## 한 일
- 파일명 경계: `_RESERVED_WRITE`가 이름 앞뒤에 ASCII 이름 문자가 이어지면 걸리지 않는다(`external_lookup_status.jsonl.md`, `-x`, `my_outputs/...`). 문장 끝 마침표와 한국어 조사(`...jsonl에`)는 이름을 끝낸다.
- 읽기·보고 허용: instruction에서 그 이름을 쓰게 하는 문장만 거절한다(`_tells_staff_to_write`). 쓰기 동사·리다이렉션·`새 파일`이 이름과 직접 이어진 경우만 쓰기로 본다. 다른 파일이 둘 사이에 있거나(동사 앞) 동사 뒤에 자기 대상 파일이 이어지면(동사 뒤) 그 동사는 이 이름의 것이 아니다. 앞선 단계의 `inputs/<단계>/outputs/<이름>`은 이전처럼 허용한다. 선언한 `outputs`·`optional_outputs`는 이전처럼 항상 거절한다.
- 시험(`tests/test_reserved_outputs.py`): 비슷한 이름 3건, 읽기·보고 6건, 쓰기 6건(6차 한국어 문장 포함), 다른 파일에 대한 쓰기 동사와 같은 이름 쓰기 구별. 옛 코드에서 10건 실패한다.

## 확인
- `test_reserved_outputs.py` 49 passed. cso·plan·output·reserved·lookup 계열 1710 passed, 9 skipped. `validate_profile` 통과.

## 하지 않은 것
- manual 한·영(#1157 P2 3번)은 이 claim 범위에 없어 쓰지 않았다. 별도 claim으로 넣는다.
- 판정은 문장 단위의 휴리스틱이라 영어·한국어 쓰기 표현을 다 덮지는 못한다(쓰기 동사 목록에 없는 표현은 허용 쪽으로 샌다). 선언된 출력은 항상 거절되므로 labhq 파일에 직원 산출물이 겹치는 일은 막힌다.

## 리뷰 후 수정 (rev3)
- P1(`cso.py:2257`): "Write records from inputs/table.tsv to outputs/external_lookup_status.jsonl"처럼 쓰기 동사와 목적지 사이에 출처 파일이 끼면 `_OTHER_PATH` 규칙이 다른 파일의 동사로 읽어 통과시켰다. 같은 부류의 변형이 한 번 더 나와, 사례를 막지 않고 구조로 닫았다.
- 구조: 지금 단계 instruction에 예약 이름이 나오면 모두 거절한다. 허용은 앞 단계 산출물 경로뿐이다. 읽기·보고 허용 휴리스틱(`_WRITE_SIGNAL`, `_OTHER_PATH`, `_CLAUSE_END`, `_tells_staff_to_write`)을 뺐다. 정상 계획이 거절되면 계획을 다시 받는 비용으로 본다(CSO 재시도 경로). 파일명 경계 수정은 유지한다.
- 시험: 읽기·보고·쓰기 6건이 모두 거절되고, 출처가 낀 쓰기·`as`·`into` 변형이 거절된다. `test_reserved_outputs.py` 50 passed, cso·plan·output·reserved·lookup 계열 1711 passed, 9 skipped, `validate_profile` 통과.
- P1(`cso.py:2272`, manual 한·영): 이 PR 범위에 manual이 없어 쓰지 않았다. 총괄이 W2의 #1155 뒤 rev4에서 넣는다.
