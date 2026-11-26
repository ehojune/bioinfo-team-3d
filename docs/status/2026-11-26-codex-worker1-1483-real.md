## 6번 비교군 ② 감사·수정 도구

Refs #1483, #1522

- `tools/prereg_kit/arm2.py`가 ① 원본과 같은 감사·수정 사본을 만듭니다. 측정 B 감사 결과에서 P1만 골라 ①의 분석 세션에서 분기한 세션에 전달합니다. 감사 1회·$4, 수정 최대 2회·회당 $5, 묶음당 $10을 예약과 비용 기록에 적용합니다.
- 실행 명령은 도구 README에 있습니다. 실감사에 필요한 달러 상한 옵션은 설치된 Codex CLI 도움말에서 확인되지 않았습니다. 실감사 전에 승인된 비용 제한 수단이 필요합니다. [도구 README](../../tools/prereg_kit/README.md)
- `python -m pytest -q tests/test_prereg_real_bundles.py tests/test_citation_audit.py` 69건 통과. 합성 묶음 세 종류에서 원본 해시 보존, P2 제외, 중복 감사와 세 번째 수정 차단, $10 경계 및 초과 청구 처리 확인. `scripts/check_public.sh` 통과. 실제 모델·자료·HPC는 실행하지 않았습니다.
- 순증 453줄. 도구·사용법 263줄, 합성 시험 174줄, 기록 16줄. 제품 코드 `labhq/` 0줄.

🤖 Co-Authored-By: Codex (GPT-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
