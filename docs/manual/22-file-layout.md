[← 매뉴얼 목차](../manual.md)

## 파일 구조

```
labhq/
  settings.py  models.py  policy.py  private_paths.py  registry.py  cli.py
  supervisor.py  store.py  durable.py  doctor.py  verify.py  request_bundle.py  reproduction_receipt.py
  portfolio.py  result_storage.py  report_contract.py  report_pdf.py  korean_qc.py
  web/          index.html·state.js·ui/*.js (2.5D 사무실) · lab3d/ (3D) · vendor/three · manifest · icon
  integrations/ github · rounds (프로젝트 이슈·정리본/보고서 커밋·공개 가드·라운드 기록·Codex 리뷰 요청)
  adapters/     base · claude_code · codex · gemini · antigravity · cli (자체 에이전트) · read_only · mock
  runner/       daemon (게이트웨이 연결·실행·잡 감시) · approvals (로컬 브로커) · workspace (실험노트·산출 스캔) · bioinfo_agent
  tools/        scheduler (SGE/PBS/Slurm) · hpc_mcp · approval_mcp · ask_mcp · annot_mcp · _mcpcompat
  gateway/      server (FastAPI · WS · REST) · shutdown (종료 처리)
  orchestrator/ cso (브리핑 → 선행 연구/파견직 → 계획 DAG → CP1/CP2 → 리뷰 → 보고)
  research/     contract (CP1·CP2·result v2) · continuation (단계 재사용) · packs (도메인 규칙) ·
                semantics* (출처/객체 그림자·입력 적합성·A/B) · semantic_device_report (의미 장치 보고서)
  evidence/     claims (주장·근거 원장 검증) · literal_check (인용·수치 대조)
  recruit/      paper2agent (논문 MCP 채용·수습·계약) · paper_skills (논문 방법론 자문 스킬)
  facilities/   signatures · fixes · packages (환경 실패 오류 서명·자동 복구 allowlist)
  vocab/        output_types.yaml (125개 key) · topic_checklists.yaml (45개 분야·135항목) ·
                edam_subset.yaml · public_resources.tsv · declare · topic_checklists
  skills/       내장 작성·도해 스킬 플러그인 (humanizer · graphical-abstract)
agents/core/*.yaml       기본 역할 13개 (Claude Code 8·Codex 5, bioinfo-agent 포함)
agents/contract/         파견직 계약서 (+ 템플릿, 호스팅 AlphaGenome 예시)
config/labhq.example.yaml
scripts/                 publish_github.sh · check_public.sh · integrations.py · vocab_tables.py · patch_notes.py
tests/                   스케줄러 파서 · 정책 · 레지스트리 · 연구 계약/의미 모델 · MCP 서버 · 어댑터(fake CLI) ·
                         GitHub 보고/정리본(fake API) · 웹(Node/브라우저) · 전체 흐름(mock)
```
