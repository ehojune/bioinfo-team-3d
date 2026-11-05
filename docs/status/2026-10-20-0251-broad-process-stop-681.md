## 결론

PR #766. 기능 커밋 `6bf40b7`·`0997a9a`로 P1 [4316642063](https://github.com/ehojune/bioinfo-team-3d/pull/766#discussion_r4316642063)·[4316642076](https://github.com/ehojune/bioinfo-team-3d/pull/766#discussion_r4316642076)의 sudo 옵션·PowerShell script block 우회를 막습니다. 이름 기반 광역 종료는 승인 카드 없이 거부합니다. Refs #681.

## 변경

- sudo 옵션의 operand·붙임값·묶인 short flag·`--`·환경변수를 넘긴 뒤 실제 명령을 검사합니다. 대소문자를 구분해 `-H`와 `-h`를 다르게 처리합니다([upstream parser](https://github.com/sudo-project/sudo/blob/main/src/parse_args.c)).
- 인용 문자열은 통째로 유지하고 `{`·`}`를 명령 경계로 읽어 script block 내부를 검사합니다. block 출력·filter·앞선 문장의 이름 선택 상태도 보존합니다([PowerShell call operator](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_operators?view=powershell-7.5#call-operator-)).
- policy·broker는 같은 guard를 유지합니다. 새 allow 분기는 없으며 명시 PID·숫자 signal·출력용 문구를 보존합니다. manual은 LF 기준 162,588→162,629자(+41)입니다.

## 검증

- 기존 head `516dbdd`에서 새 문자열/fake broker 회귀 31개 실패를 재현했습니다. block 출력·filter 연결 회귀 9개도 각 수정 전 실패를 확인했습니다. 최종 전용 test 144 passed, 승인 정책의 기존 정적 test 24 passed.
- fake broker는 두 우회를 카드·event·submit handler에 보내지 않고 pending 기록도 남기지 않음을 확인합니다.
- 최초 setup은 `markdown_it` 부재로 실패했습니다. 기존 임시 의존성을 읽기 전용 PYTHONPATH로 사용했으며 설치·공용 venv 변경은 없습니다.
- Python 3.10 AST·공개·diff·patch-notes·notes 형식 검사 PASS. patch-note는 중첩 경로를 금지하는 notes gate에 맞춰 기존 `patch_notes/entries/broad-process-stop-681.yaml`에 둡니다.

## 남은 것

검증은 문자열·fake·정적 검사뿐입니다. 실제 shell/process/proc_tree integration·PI 운영·HPC·원자료·분석은 실행하지 않았습니다. 이전 검증 범위 이탈 기록은 [기존 보고](2026-10-19-2341-broad-process-stop-681.md)에 보존합니다. PID tracker #675·동적 명령 OS 격리는 별도 범위입니다. P2 positional PID 배열은 collector가 issue로 추적합니다. push 뒤 CI snapshot만 확인하며 CI 대기·새 head 리뷰·병합·배포는 supervisor 담당입니다.
