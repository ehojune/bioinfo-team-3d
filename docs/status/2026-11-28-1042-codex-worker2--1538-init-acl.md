## Windows에서 기존 인스턴스의 init 재실행 검사

Refs #1538

- 기존 인스턴스에서 `init`을 다시 실행하면 `doctor`가 작업 폴더의 ACL을 읽습니다. Windows에서 이 호출을 막던 테스트 fixture 때문에 실패했습니다.
- 재실행 테스트에서 해당 조회만 모의 처리합니다. 설정 파일의 바이트가 같고 작업 폴더를 한 번 조회해 경고를 표시하는지 검사합니다. 제품 코드는 그대로입니다.
- Windows 로컬에서 수정 전 실패를 재현했고, 수정 후 인스턴스·doctor·init 관련 pytest 242건이 통과했습니다. ACL 응답은 모의 값입니다. 시험용 직원 교대와 PI 배포는 병합 SHA의 CI 통과 뒤 진행합니다.

🤖 Co-Authored-By: Codex (GPT-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
