# 외부 조회 시간 상한 #676

- 기존 ChIP-Atlas는 호출별 `wait_s`만 제한하고 시간 초과 뒤 재호출을 권했다. 단계 전체 상한·대기 알림·실패 산출 기록은 없었다.
- 일반 `labhq_annot` API 두 경로(`_run`, `_run_literature`)는 단계 첫 조회부터 1,200초의 wall-clock 기한을 공유한다. `.labhq/external_lookup_budget.json`으로 MCP 재시작·서비스 변경 뒤에도 기한을 유지한다. 조회 사이 계산 시간도 이 기한에 포함된다.
- 시작·60초 간격·종료를 기존 task 인증 `/event`의 `agent.log`로 보내고 `outputs/external_lookup_status.jsonl`에 상태·경과·상한만 쓴다. 입력·응답·토큰은 기록하지 않는다. 기록 경로의 기존 owned guard를 유지한다.
- 시간 초과는 MCP 오류이며 NOT_EVIDENCE 문구를 유지한다. ChIP-Atlas 자체의 짧은 상한도 timeout으로 기록한다. 직원 규칙은 자동 재시도를 멈추고 남은 작업과 빠진 조회 보고를 요구한다. checkpoint·게시·승인 판정은 바꾸지 않는다.
- `key_fulltext`의 fulltext(OA·OpenAlex·Unpaywall 포함), 기관 요청 간격 대기와 fetch도 같은 예산을 쓴다. 명시적인 PI PDF 승인 대기 시간만 제외하며, 승인 전 사용한 예산은 이후 서비스 조회에도 유지한다. 승인·기관 접근 허용 조건은 바꾸지 않는다. 직원이 직접 작성한 스크립트와 타사 MCP는 이 기한의 강제 대상이 아니며, 스크립트에는 별도 wall-clock timeout 작성 규칙을 준다. 전체 #676을 이미 해결했다고 닫지 않는다.
- 검증: 새 coroutine/owned-file/mock HTTP 및 기존 ChIP-Atlas MockTransport만 21 passed, 26 deselected. 기한 재설정 방지·대기 알림·오류/부재 구분·취소 전파·owned guard·인간 승인 경로 제외를 확인했다. P1 후속은 수정 전 자동 조회 3경로에서 실패를 재현했고, 수정 후 관련 mock/static 40건이 통과했다. 승인 거절·만료·취소 뒤 예산 보존과 기존 PDF 접근 검사를 포함한다. 실제 서비스·프로세스/proc_tree·기관·HPC·원자료·운영·PI 인스턴스는 실행하지 않았다.
- manual 163,011→163,275자(+264, LF Unicode). 상세 근거는 lookup-only로 보관한다.
- PR #776 P1 후속 manual 163,275→163,348자(LF Unicode). 실제 기관·브라우저·프로세스는 실행하지 않았다.
