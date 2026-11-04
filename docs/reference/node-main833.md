# #833 — browser startup와 정리 증거

[Main run39632988754](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39632988754/job/119336016451)는 두 pure TAP subtest 뒤30초에 실패했습니다. 보존된 failure slice에는 stderr의 `desktop open`·`open-start`가 있습니다. 세 번째 browser subtest가 시작된 것이며 전체 단언 완료나 Node VM/channel 누수의 증거가 아닙니다. Profile·server·Chrome·CDP 중 어느 startup 단계가 지연됐는지는 아직 모릅니다. 이전 chrome-stdio5초 cleanup 실패와도 구분합니다.

별도로 소스에서 확인한 정리 누락을 고쳤습니다. Profile 생성 뒤 server 획득·reserved-port 거부를 try/cleanup 안에 두고, listen 오류를 startup 호출에 돌려줍니다. Server를 얻지 못한 실패는 profile만 정리하고, 얻은 뒤 거부되면 실제 server close 후 정리합니다. 원래 오류와 cleanup 오류를 함께 보존합니다. 이 누락이 해당 main timeout을 일으켰다고 단정하지 않습니다.

Startup에 profile/server/spawn/debug-port/target/socket/protocol 경계를 추가했습니다. 기존16행 상한 안에서 한 번의 startup+load15행을 기록하며 timer·ready 판정을 만들지 않습니다. UI 단언·격리·30초와 기존 cleanup/CI 상한·필수26·receipt 검증은 유지합니다. 동기 trace/등록 probe6·기존 wrapper/static11은 통과했습니다. 실제 driver를 격리 VM에서 검사하는 비동기 정리 계약3건과 native 종료는 새 PR 및 그 main에서 확인해야 합니다. #833은 미해결입니다.

## Same-tree reuse

PR832의 base12609d20과 merge9b94ccc7은 Windows profile blob이 `4c7c9aa423de708d3bd55bd982f9c7f78c495e2c` → `6ef06fcf40a333378a12131d899b6548aada7814`로 다릅니다. 변경은 scheduler-none test의 inventory/cohort 등록입니다. 기존 `valid_run()`은 producer policy가 PR base의 main에 이미 있어야 하므로 이 증거를 거부합니다. Tree812cd079 일치만으로 이 조건을 넘을 수 없습니다.

인증된 PR run metadata와 Git blob을 넣은 동기 probe도 이 guard에서 거부됐습니다. 해당 probe의 event.before 일치는 가정이며 실제 changes 실행의 최초 반환 지점을 재현한 것은 아닙니다. 그래도 policy 불일치만으로 재사용 부적격은 확정됩니다. CI/helper 정책과 trust gate는 바꾸지 않았고 알려진 실패를 reuse로 숨기지 않습니다.
