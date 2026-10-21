# 게시 경로 검사 비용 — PR #825 보강안

기존 Linux 3.12 실패는 731,072자 입력의 CPU 2.0093초였다. `/` 65,536개·역슬래시 65,536개·`:` 600,000개, 정확한 출력과 `<2` CPU초 assertion을 유지한다. 이 입력 하나의 시간 검사는 모든 입력의 선형성을 증명하지 않는다.

`_sanitize_path_line`에서 전체 줄이 `/`, 역슬래시, `:`만인지 C 정규식 `fullmatch`로 검사한다. 모든 restricted zone에 다른 문자가 있다면 이 줄은 그 zone을 나타내지 못한다. 이 경우에만 Python path 후보 순회를 생략한다. `/:`처럼 구분자만 있는 zone이나 다른 문자가 하나라도 있는 줄은 기존 classifier에 전체 길이로 전달한다. policy scanner·secret/URL/auth 검사·출력 제한 순서는 바꾸지 않았다.

검증: 새 work 회귀 4건 red 뒤 지정 pure 검사 43건 PASS. 기존 path classifier와 6,320개 조합의 출력이 같고, 긴 접두사 뒤 POSIX/Windows/UNC/Unicode·file URI·public 경로와 credential을 확인했다. 기존 test 함수 36개와 CPU 거부 검사의 AST도 그대로다.

같은 Windows 로컬 입력의 단일 관측은 0.078125→0.015625 CPU초였다. 프로파일 함수 호출은 1,527,855→107회로 줄었다. 타이머 해상도·호스트가 달라 Linux CI 통과나 일반 성능 보장으로 해석하지 않는다. 원래 2초 초과는 로컬에서 재현되지 않았다. 실제 Git/process/Node·운영·네트워크 검증은 수행하지 않았으며 새 head의 CI는 root가 검토 뒤 판단한다.
