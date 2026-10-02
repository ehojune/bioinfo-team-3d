## 2026-10-02 · #301 — bioinfo-agent 질문 게이트와 파이프라인 PR

- 결론: bioinfo-agent의 일반 질문과 새 파이프라인 생성 판단은 CSO가 답한다. 통제 데이터 구역·비용 상한 초과·프로그램 설치만 PI 승인으로 보낸다. 공개 bioinfo-agent 저장소로 가는 자동 PR은 기본 꺼짐이고 `policy.bioinfo_agent.pipeline_pr: true`로 켠다(PI 결정, #300).
- 바뀐 것: 켜면 gateway가 새 파이프라인 산출을 검사해 branch와 PR로 올린다. 꺼져 있으면 gateway가 러너에 파일을 요청하지 않고 GitHub도 부르지 않는다. 직원에게 GitHub 토큰을 주지 않는다. 파일은 pipeline 소스 종류와 https 테스트 데이터를 가리키는 `assets/samplesheet*.csv`만 받고, 절대경로는 접두어 목록 없이 모두 거부한다(`#!` 인터프리터와 `/dev/null`류만 예외). PR 상태는 snapshot에도 실려 늦게 접속한 화면에도 남는다.
- 실행한 것: 관련 pytest 536건 통과·4건 제외, 웹 상태 43건, 공개 검사를 통과했다. README는 51,970자에서 52,201자로 231자 늘었다. 전체 pytest는 CI에 맡겼다.
- 미해결: 켤 때 gateway 토큰에 bioinfo-agent 저장소의 Contents·Pull requests 쓰기 권한이 있어야 한다. 다른 labhq 사용자에게 기여 여부를 묻는 일은 #300. 컨테이너 안 `/opt/...` 경로나 README 예시 `/data/...`가 있는 pipeline(예: 기존 pacbio-hifi-wgs)은 거부되어 PR이 열리지 않는다.
- 근거: `labhq/pipeline_pr.py`, `labhq/integrations/github.py`, `labhq/gateway/server.py`, `tests/test_bioinfo_pipeline_pr.py`.
