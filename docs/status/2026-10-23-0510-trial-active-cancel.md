## 변경
자동 monitor의 terminal→PI 취소 race를 막고 요청 소유 task attempts를 읽는 변경안입니다.

- `/cancel-active`는 exact created_at·현재 active 상태를 await 없이 확인합니다. terminal은 PI 결정 없이 생략하며 구판404 뒤 legacy 취소로 fallback하지 않습니다. 수동 취소는 유지합니다.
- 인증된 `/task-attempts`는 durable briefing/review/retry 전체와 같은 snapshot의 비용·hold를 묶습니다. accounting/result에만 있는 ID는 누락 UNKNOWN입니다. identity 오류·1000행·1MiB 초과는 incomplete입니다. prompt/argv/workdir는 반환하지 않습니다.
- cleanup은 허용필드만 반환합니다. merged TaskResult 저장 경로를 확인했으며 Job close 계약과 측정된 ActiveProcesses0을 구분합니다. budget_holds 의미는 미병합 #865에 의존합니다.
- fake 취소7건·projection10건 PASS. 실제 HTTP/process/auth/model/연구는 미실행이고 runtime은 UNVERIFIED입니다. Windows inventory는 순수 test로 등록했고 기존 안전 경계는 유지합니다.
- main0f80516(#864 포함)을 통합했습니다. manual 충돌1건은 취소/cleanup 설명을 모두 보존했습니다. CI는 총괄 watcher가 확인합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
