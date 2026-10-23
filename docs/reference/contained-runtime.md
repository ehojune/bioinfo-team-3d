# Contained runtime readiness

`contained_verified`는 **현재 gateway·runner와 각각의 supervisor 네 세대**만 확인한다. 직원 CLI·손자 프로세스, 요청 전체 종료, OS process 0, 연구 GO를 뜻하지 않는다. Refs #877.

기본 detached 정책은 그대로다. 새 경로에는 state의 `contained-runtime.json`, **state 밖에서 운영자가 별도로 고정한 계약**, 살아 있는 owner의 fresh 응답이 모두 필요하다. state 파일과 owner의 자기응답만으로 PASS하지 않는다.

## 신뢰와 검증

- doctor 호출자가 `LABHQ_CONTAINED_TRUST_PATH`와 `LABHQ_CONTAINED_TRUST_SHA256`으로 외부 계약을 선택한다. owner가 이 계약이나 pin을 발급·선택하면 안 된다. 같은 OS 계정의 악의적인 운영자가 계약·실행물을 위조하는 공격은 이 경계 밖이다.
- 외부 계약은 현재 owner PID·생성 시각, 실행 이미지 hash, 검토한 launcher와 시작 계약의 실제 파일 hash, nonce·네 역할·설정·provenance·마감을 고정한다. 발급 후 최대 300초이며 cohort 마감보다 늦을 수 없다. 독립적으로 고정하지 않은 hash 자기일치는 근거가 아니다.
- doctor는 owner의 PID·생성 시각·이미지·생존을 조회 전용 handle로 확인한다. 연결된 pipe의 커널 server PID가 그 owner인지도 확인한다. Job handle은 열거나 넘겨받지 않는다. 과거 명령의 임의 재구성 대신 운영자가 추적한 owner 세대와 검토된 launcher pin을 신뢰한다.
- 매번 새 challenge와 nonce를 보낸다. owner는 현재 네 PID·생성 시각을 고정한 handle로 **자신의 정확한 Job** 포함과 생존을 조회해야 한다. 재시작으로 세대가 바뀌면 receipt도 새로 발급해야 한다.
- owner 응답은 독립 timer 활성, 미래 마감(발급 후 최대 5400초), KILL_ON_JOB_CLOSE와 breakaway 금지, config·source·contract·launcher·검사 코드 provenance를 묶는다. 검사 코드의 **배포 경로와 바이트 hash**는 실제 실행 중인 모듈과 대조한다. 과거 frozen doctor는 이 경로 자체가 없으므로 PASS할 수 없다.
- 응답은 3초 이내여야 한다. 읽기·쓰기는 message pipe NOWAIT이며 전체 대기도 최대 3초다. owner 소멸·PID 재사용·기한 만료·조회 오류는 실패다.
- 새 경로가 대체하는 이유는 Job escape 실패뿐이다. 이력 오류와 기존 인증·runner 연결 실패는 남는다. 실제 Job membership은 계속 `true`로 보고한다.

## receipt와 RPC

| receipt 필드 | 계약 |
|---|---|
| schema / issuer | `labhq-contained-runtime/1` / `labhq-contained-owner/1` |
| config_path / config_sha256 | 현재 설정의 실제 경로와 SHA256 |
| nonce | 소문자 hex 32–64자리 |
| owner | `{pid, created: "windows:<FILETIME>"}` |
| roles | `gateway`, `gateway-supervisor`, `runner`, `runner-supervisor`의 현재 `{pid, created}` 정확히 네 개 |
| issued_at / deadline_at | 유한한 epoch 초; 현재가 이 구간 안이고 길이는 최대 5400초 |
| pipe | `\\.\pipe\labhq-contained-<32자리 hex>`; 원격 pipe 금지 |
| provenance | `source_commit` 40자리, `contract_sha256`, `launcher_sha256`, `runtime_readiness_sha256` 64자리, 실제 `runtime_readiness_path` |

외부 계약은 `schema: labhq-contained-trust/1`, `issuer: operator-reviewed`, `issued_at`, `expires_at`, receipt와 일치하는 `owner`, `nonce`, `roles`, `config_sha256`, `deadline_at`, `provenance`를 담는다. `launcher`, `launch_contract`, `owner_image`는 각각 `{path, sha256}`다. 파일은 state 밖에 두고 운영자가 검토 후 정확한 SHA를 doctor 호출에 전달한다. owner가 쓴 receipt에서 pin을 자동 복사하지 않는다.

요청은 `schema: labhq-contained-query/1`, 새 `challenge` 32자리, receipt의 `nonce`, `owner`, `roles`, `config_sha256`, `deadline_at`, `provenance`다.

응답은 `schema: labhq-contained-snapshot/1`과 동일 issuer·challenge·nonce·owner·config hash·deadline·provenance를 포함한다. 추가 필드는 `observed_at`, `deadline_active: true`, 실제 `job_limit_flags`, 네 역할의 `{pid, created, alive: true, in_job: true}`다. owner는 과거 armed 파일을 그대로 되돌려주면 안 된다.

## 검증 범위

공개 테스트는 fake handle·pipe·clock으로 정상 판정과 외부 pin 누락/변경·배포 경로·이미지·nonce·세대·마감·Job·인증 실패를 검사한다. 실제 B·직원 CLI·process 생성/종료·연구 요청은 실행하지 않았다. 새 verifier 배포와 owner RPC 구현·재기동·실제 검사는 별도다. 현재 frozen B가 자동으로 PASS한다고 주장하지 않는다.

연구 GO 전에는 별도 disposable dummy CLI와 실제 adapter 경로의 손자 포함, owner crash·deadline 종료 receipt가 필요하다. third-party WMI·service 우회는 미증명이며 이 판정의 범위가 아니다.
