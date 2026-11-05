## 단계
#1132(#1125 E3): PI 문장 번역 결과의 쓰기를 한 transaction으로.

## 한 일
- `labhq/pi_language.py` `_interpret`의 `finally`: `pi_translation` 행, 메시지 `pi_display`(`set_message_display`), 승인 카드 복사본 저장을 `store.arun_batch`(#1131) 한 transaction으로 했다. 승인 행은 loop thread에서 deep copy한 것만 writer thread가 쓴다(live gateway 상태를 writer thread가 읽지 않음).
- 뒤따르는 `persist_request`는 없앴다. 바로 이어지는 `messenger.translated` 발행(#1130)이 같은 request를 한 transaction으로 저장한다.
- 번역 1건의 실제 COMMIT이 4회에서 2회가 되고, 동기 `put`이 event loop를 막던 것도 없어졌다. 재시작 뒤 번역 결과·표시·카드 사본이 서로 어긋날 틈이 없다.
- 시험(`test_gateway_publish_durable.py`): COMMIT 2회, 두 번째 쓰기 실패 시 첫 쓰기도 없음. 둘 다 옛 코드에서 실패함을 확인했다. 기존 `test_pi_language*`·`test_messenger_history`·`test_approval_durable_606`은 그대로 통과(235 passed 포함).

## 실측 (D: 드라이브, FULL, lab 요청 1회)
| | transaction | commit 시간 합 | 요청 시간 |
|---|---|---|---|
| main | 659, 657 | 18.9 s, 19.0 s | 23.4 s, 23.9 s |
| 이 브랜치 | 663, 669 | 18.6 s, 18.3 s | 23.2 s, 23.3 s |
lab 요청에는 번역이 나오지 않아(번역은 기술적인 영문 메시지에만 붙음) **차이가 없고 오차 안입니다.** 이 측정으로는 E3의 효과를 보일 수 없습니다. 효과는 번역 1건당 실제 COMMIT 4회 → 2회(시험으로 확인)입니다. 디스크 fsync가 앞선 측정(commit 합 34 s)보다 빨라 이 드라이브의 상태가 달라졌습니다.

## 하지 않은 것
- lab 요청 1회에 번역이 몇 건 나오는지는 요청에 따라 달라 효과가 작을 수 있다. 번역이 없는 요청에서는 차이가 없다.
- E2(`pending` 표시를 이벤트와 함께)는 이번 범위가 아니다.
