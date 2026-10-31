# #1207 rev8: 팀 모드 단계 실패와 모든 갈래 실패가 어떻게 끝나는지 기록한다

- 결론: 요청이 어떻게 끝나는지 e2e로 고정했다. 제품 결함은 찾지 못했다. #1220의 판단 근거로 쓴다.

| 경우 | 끝 상태 | 단계 결과 | 보고·카드 |
|---|---|---|---|
| 팀 모드(`work_kind=simple`), 3번째 단계(bioinfo-agent) 실패 | `failed`, outcome 없음, error `s3 실패: mock step failure` | s1·s2 성공, s3 실패, s4·s5는 `skipped: upstream …`(실행 안 됨) | 한 번 replan한 뒤 끝남. 보고서는 `실패한 단계: s3 · bioinfo-agent`로 시작. 승인 카드 0개 |
| 연구 모드, 모든 갈래(s1·s2) 실패 | `failed`, outcome `research_failed` | s1·s2 실패, 종합 단계 s3은 `skipped: upstream s1…, s2…` | CP2 카드 없이 CP1(`research_plan`) 뒤 바로 끝남. 보고서에 `갈래별 terminal 상태` 표(b1·b2 모두 failed, 이유·마지막으로 끝난 단계) |
| (rev6) 연구 모드, 한 갈래만 실패 | `done`, outcome `research_reported` | 다른 갈래와 종합 단계는 실행 | 실패는 보고서 한계와 CP2 경고로만 보임 |

- 관찰: 일부 실패를 허용하는 곳은 연구 모드뿐이다. 팀 모드는 한 단계가 실패하면 요청 전체가 실패한다. 연구 모드도 살아 있는 갈래가 하나도 없으면 실패다.
- 바뀐 것: `labhq/adapters/mock.py`(`[step-fail]`을 팀 계획의 bioinfo-agent 단계에, `[all-branch-fail]`을 분기 계획의 두 갈래에 적용), `tests/test_e2e_mock.py`(helper에 `work_kind`, 시험 2건).
- 실행한 것: `test_e2e_mock`(13) 로컬 통과. 새 시험 2건은 옛 mock에서 실패함을 확인.
- 미해결/미검증: manual의 mock 토큰 단락에 `[all-branch-fail]`·팀 모드 `[step-fail]`은 아직 없다(claim 밖). 갈래 안 여러 단계, 재시도, replan이 성공하는 경우는 확인하지 않았다.
