## 단계
#869 — 채용·논문 스킬 기능의 명시적 비활성화

## 한 일
- `recruit.enabled`는 strict boolean이며 기본 true입니다. false이면 채용·설치·계획 준비·지연 작업·논문 스킬 부착을 거부합니다.
- gateway/runner에서 복구·위조 task도 작업 폴더나 모델 실행 전에 거부합니다. 계약 활성화·연장·재고용은 막고 해제는 허용합니다.
- doctor는 명시적으로 꺼진 Paper2Agent만 skip하고 --fix도 설치하지 않습니다. 기본 recruiter 누락과 다른 인증·runtime 검사는 유지합니다. 이미 발생한 변환 비용은 보존합니다.

## 테스트 결과
- 추출한 실제 함수와 fake 경계를 쓰는 새 검사 14건, 기존 paper-skill 정적 검사 9건 PASS. 제품/app/Hub/runner import와 event loop 없이 검사했습니다.
- Python 3.10 AST 9개 파일, integrations --check, public·diff 검사 PASS.

## 막힌 점
실제 설치·runtime·모델·network·process·Job 검사는 하지 않았습니다. CI·독립 검토·병합은 총괄 담당이며 이 PR만으로 운영 준비나 GO를 주장하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>