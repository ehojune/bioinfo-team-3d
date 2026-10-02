## 2026-10-02 · #301 — bioinfo-agent 질문 게이트와 파이프라인 PR

- 결론: bioinfo-agent의 일반 질문과 새 파이프라인 생성 판단은 CSO가 답한다. 통제 데이터 구역·비용 상한 초과·프로그램 설치만 PI 승인으로 보낸다.
- 바뀐 것: 새 파이프라인 산출을 gateway가 검사해 공개 bioinfo-agent 저장소에 branch와 PR로 올린다. 직원에게 GitHub 토큰을 주지 않고, 데이터·통제 경로·로컬 절대경로가 있으면 거부한다. 권한 부족은 웹에 PR 대기로 남긴다.
- 실행한 것: 관련 pytest 210건 통과·2건 제외, 웹 상태 43건, integrations 검사, 공개 검사, diff 검사를 통과했다. README는 52,194자에서 52,523자로 329자 늘었다. 전체 pytest는 지시대로 CI에 맡겼다.
- 미해결: gateway 토큰에 bioinfo-agent 저장소의 Contents·Pull requests 쓰기 권한이 있어야 자동 PR이 열린다. CI와 봇 리뷰는 총괄이 이어받는다.
- 근거: `labhq/pipeline_pr.py`, `labhq/integrations/github.py`, `labhq/orchestrator/cso.py`, `tests/test_bioinfo_pipeline_pr.py`.
