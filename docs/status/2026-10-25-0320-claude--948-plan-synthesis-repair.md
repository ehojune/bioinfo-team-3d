Closes #948

12차(`req_a52fd60207`)가 CP1 전에 "synthesis 단계가 갈래 끝을 받지 않음"으로 끝났습니다. 증거 DB의 두 계획(`task_72510404d7`·`task_8ffaef9bae`)에서 원인을 찾아 세 가지를 고쳤습니다.

- **결정적 보정:** `branch: "synthesis"` 단계가 있는데 첫 synthesis 단계가 각 갈래의 마지막 단계를 `depends_on`으로 받지 않으면, 검사 전에 labhq가 채우고 계획 `warnings`에 남깁니다. synthesis 단계가 없거나 채우면 순환이 되는 경우는 고치지 않습니다(`repair_synthesis_links`).
- **새로 찾은 원인:** 두 계획 모두 CSO가 `synthesis`라는 이름의 갈래를 함께 선언했습니다. 그러면 올바르게 연결한 첫 계획도 "synthesis가 자기 갈래의 끝 단계를 읽어야 함"과 "갈래 단계는 갈래 밖을 못 읽음" 두 규칙에 걸려 거절됩니다. synthesis를 예약 lane으로 보고 두 규칙에서 뺐습니다.
- **교정 3회:** 계획 교정을 1회에서 3회로(첫 계획 + 3회 = 4번), 매 회 남은 문제 원문을 줍니다. 3회 뒤에도 실패하면 보고서에 실제 횟수("3번 고친")를 씁니다.
- 테스트 `tests/test_plan_synthesis_948.py` 8건: 12차 모양 보정 후 통과, 기존 의존성 유지, 갈래 이름 synthesis 허용, synthesis 단계 없음은 보정 없음, 순환 방지, 두 번째 교정에서 통과해 CP1 생성, 3회 뒤 실패 보고, 보정만으로 교정 호출 없이 승인. 기존 3개 테스트의 시도 횟수를 2에서 4로 갱신했습니다. 관련 묶음 979건 통과(로컬, Windows, Python 3.12).
- 문서: `docs/manual.md` 계획 검증 절.
- 미검증: 실제 모델이 새 경로로 CP1 카드까지 가는지(13차가 첫 근거). 일반 lane의 교정 횟수(2회)는 바꾸지 않았습니다.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
