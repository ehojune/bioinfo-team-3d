Refs #606 — runner `contract.update` 저장 경계.

- 지연 mock에서 idle/active writer 모두 heartbeat 정지 2건을 재현했습니다.
- 기존 FILES writer에서 계약 snapshot을 저장하고 성공 뒤 Registry·roster를 반영합니다. 같은 계약의 갱신은 저장·반영까지 직렬화하며 반복 취소 뒤에도 성공·오류를 확인합니다.
- 관련 mock/static: **132 passed, 7 subtests passed**. 두 복사본·lifecycle·오류·취소·경쟁·재로드·rehire warning 검증입니다. 같은 ID의 recruitment가 먼저/나중에 저장되는 경쟁과 sync plan-rehire 3건도 실패→통과를 확인했습니다.
- 각 파일의 atomic write·fsync 보장은 유지합니다. 두 파일 사이 저장 오류의 rollback은 추가하지 않았고 실패 때 Registry·roster를 갱신하지 않습니다. 재시도로 두 파일이 수렴하는지 확인했습니다.
- gateway/store/job/task 원장은 변경하지 않았습니다. 실디스크 latency·실프로세스 재시작·설치·배포·연구·HPC는 미실행입니다. #606은 열어 둡니다. 공통 manual 통합은 W2 뒤 별도 범위입니다.
- public/diff 검사와 최종 head CI·알려진 P1·충돌 확인 뒤 담당 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
