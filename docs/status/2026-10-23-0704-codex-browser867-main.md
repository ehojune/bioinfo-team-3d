## 단계
PR #867 진단 전용 branch에 최신 main 통합

## 한 일
- main f5f56b67ff7220bd86e775f6c3f209f35a98edc3(#865 포함)을 충돌 없이 merge했습니다.
- 자동 merge-tree 47011c70a8ad6e6dd1758c4aae5fbeaa79b83fc5와 merge 결과가 같습니다. 기존 작성자 이력과 진단 코드·test 4개 blob을 보존했습니다.
- 제품 변경은 main에서 온 내용뿐입니다. 브라우저 시작 표시와 대기 수치는 진단이며 원인·시작 성공을 증명하지 않습니다.

## 테스트 결과
- in-process fake Job/start 12건, 변경 Python 2개 3.10 AST PASS. 실제 Job을 불러오는 test는 실행하지 않았습니다.
- notes_index validate, patch_notes check, public check, diff check를 확인합니다.

## 한계
실제 browser·Node·process·Job·전체 pytest는 실행하지 않았습니다. CI와 병합은 총괄 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>