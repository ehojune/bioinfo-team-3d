Refs #983. `runner.facilities_support`로 시설팀 지원 요구를 선언합니다. 기본은 `unspecified`이며 기존 설치와 init `--force`의 명시 값을 보존합니다.

- required의 부재·실패·미확인·dry-run·mock은 FAIL, optional+ready만 OK, unspecified는 항상 WARN입니다. 다른 직원 FAIL과 기존 login warning·runtime admission은 유지합니다.
- 기존 version/login·시설팀 개별 preflight 증거만 재사용합니다. timeout/미지원·판별 불가는 unverified, 명시적 비인증은 blocked입니다. 추가 probe·직원 추가는 없습니다.
- 기존 doctor 비교: 지원 행 부재로 7 failed. 수정 후 관련 mock/static 273 passed, 추가 force_engine 2건을 포함한 최종 시설팀 회귀 60 passed(앞 검사와 중복). 21조합·직원별 증거·custom contract/만료·무쓰기·init 보존·CLI exit를 확인했습니다. #985/#989 회귀도 통과했습니다.
- Python 3.10 syntax·public·diff·Windows inventory 검사 통과. manual은 LF 기준 194,939→195,168자입니다. 상세 판정표와 한계는 [reference](../reference/facilities-support-983.md)에 있습니다.
- #990의 구현 종료·최종 head 리뷰 요청 뒤 manual 한 문단을 반영했습니다. 최종 head CI·같은 head 실제 review·알려진 P0/P1 수정검증·head guard 뒤 담당 병합하며, 대기는 hold로 표시합니다.
- 실제 설치판 복구는 **UNVERIFIED**, #983 OPEN입니다. 운영 설정/roster·직원/daemon·설치·배포·연구·HPC는 실행하지 않았습니다. token/provider 사용량은 미측정입니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
