## 2026-10-03 · runner — Windows OS 신뢰 저장소를 직원 Python에

- 결론: 9차 모의 시운전에서 Codex engineer가 Enrichr 접속 TLS 검증 실패로 멈췄다. 원인은 기관 TLS 검사 장비(발급자 SOOSAN INT)의 루트가 Windows 저장소에만 있고 certifi에는 없는 것이다. runner가 두 쪽을 합친 CA 묶음을 직원 env에 넣는다. 검증은 끄지 않는다.
- 바뀐 것: `labhq/runner/system_ca.py`(certifi + Windows ROOT·CA 저장소 PEM), `Runner._system_ca_env` — workspace 루트에 `.labhq-system-ca.pem`을 한 번 쓰고 `SSL_CERT_FILE`·`REQUESTS_CA_BUNDLE`을 지정한다. PI가 runner 환경이나 `engines.*.env`에 둘 중 하나를 정했으면 건드리지 않는다. `runner.system_ca_bundle`(기본 true, Windows에서만 동작).
- 실행한 것: 전체 test 3539 passed. 새 runner test 6건은 수정 전 실패. 이 PC에서 내보낸 묶음으로 maayanlab.cloud·data.broadinstitute.org·string-db.org 검증 통과를 직접 확인했다.
- 미해결: 없음.
- 근거: `tests/test_system_ca.py`.
