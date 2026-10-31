## 단계
#1220: 단계·갈래 일부가 실패해도 목록에 `done`으로만 보이는 문제. 설계는 [#1220 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1220#issuecomment-6546115060)이고 총괄이 파생 표지 안으로 정했다.

## 바뀐 것
- `request_status.py`: `failed_step_ids(request)`(`done` 요청에서 실패·건너뜀·한 번도 돌지 않은 단계, 이미 `failed_steps`가 있는 요약은 그대로 씀)와 `request_display_label`의 `완료 · 일부 실패 N`. 저장 필드·새 상태·새 outcome 값 없음.
- `gateway/server.py`: `request_summary.failed_steps`, 스냅샷의 요청별 `failed_steps`.
- `cli.py`: `labhq watch` 완료 줄에 `일부 실패 B1, B2`, `labhq status` 최근 완료 요청에 `완료 · 일부 실패 N`과 `일부 실패한 단계: …`(`_finished_request_lines`; 묶음이 없어도 실패 단계가 있으면 나온다).
- `orchestrator/cso.py`: `request.completed` 이벤트 데이터에 `failed_steps`(있을 때만, 이벤트에만 있고 요청에는 저장하지 않음), 연구 보고서 한계 줄에 `B1 (갈래 B): 단계 실패 …`.
- 웹(`state.js`·`index.html`): `partialFailureSteps`와 상태 문구 `완료 · 일부 실패 N`, 요청 목록의 경고색(`status-halted`의 기존 색 재사용). 스냅샷의 `failed_steps`를 쓰고 라이브 요청은 단계 상태로 같은 판정을 한다.
- PDF 머리말과 캐시 키는 `request_display_label`을 이미 써서 코드 변경 없이 같은 표지를 쓴다(캐시 키가 라벨을 포함해 표지가 바뀌면 다시 만든다).
- manual 한·영의 "요청 수명과 PI 검토" 절에 표지를 적었다. Windows inventory에 새 시험 추가.

## 시험
- `tests/test_partial_failure_1220.py` 13건: 도우미(완료만, 직접 요청, 요약 그대로), 라벨, 요약이 단계 진행을 바꾸지 않고 요청에 쓰지 않음, watch·status, 갈래를 잃은 연구 요청의 한계 줄과 완료 이벤트(깨끗한 요청은 표지 없음), 웹 문구와 경고 판정(node), PDF 머리말과 캐시 키.
- 관련 688 passed, 2 skipped(`test_request_status.py`·CLI·follow-up·github reporter·web·PDF 포함).

## 하지 않은 것(총괄 지시)
- 결과 저장 README에 같은 줄을 넣는 것, 비연구 모드에서 단계가 실패해도 `done`이 되는 경로 확인.
- 알림 팝업.
- 웹 화면의 실제 렌더링(브라우저 확인)은 하지 않았다. 문구 계산만 node로 확인했다.
