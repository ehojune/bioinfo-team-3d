# Windows 검사 대기 경계 (#898)

| 경계 | 관측·수정 | 남은 한계 |
|---|---|---|
| R version / syntax | PR #885 head `58a41aeb`의 syntax subprocess가 기본 10초를 초과했다. 전체 call 19.89초는 version도 포함한다. 이제 test fixture가 두 subprocess의 elapsed·timeout·returncode를 따로 기록한다. | R 기동·parse·자원 경쟁 중 원인은 미확인. 로컬 Rscript가 없어 실제 R 두 case는 skip됐다. |
| mock signal / ACK | [run 39904361507](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39904361507)의 3초 gather가 durable ACK 대기 중 취소됐다. work 준비를 먼저 확인하고 result·gateway·ACK·entry를 각각 5초 이내로 기다리며 단계 시간을 기록한다. | 제품 shutdown의 5초는 그대로다. 각 fixture 단계의 기한이며 요청 전체 종료 기한이 아니다. 실제 OS signal은 보내지 않는다. |

ACK에만 3.1초 지연을 넣은 로컬 비교에서 기존 검사는 `TimeoutError`로 실패했고 수정본은 통과했다. 이 비교는 전체 3초 상한의 문제를 보여준다. 원래 CI의 DB·writer 지연 원인을 재현한 증거는 아니다. 지연 주입기는 로컬 실험에만 썼다.

R fake 검사는 version timeout 뒤 syntax PASS와 syntax timeout을 구분한다. 두 호출의 10초, `not_rerun`, 기존 syntax·shutdown·resume·outbox 단언과 coverage/receipt 계약을 유지한다. 진단에는 command·source·환경·절대 경로를 넣지 않는다.

R 실제 지연 원인이 남아 있으므로 이 PR만으로 #898을 닫지 않는다. 다음 실패의 단계별 기록으로 원인을 좁힌다.
