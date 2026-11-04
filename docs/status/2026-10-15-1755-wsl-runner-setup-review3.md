## 결론

Refs #630. 두 P1의 경로 변환을 모델 선언과 runner 경계 함수로 통합했다. gateway가 Linux여도 WSL 결과를 Windows runner에 전달한다.

## 바뀐 것

| 지적 | 수정 |
|---|---|
| P1 4280657526 | 공유 경로는 gateway OS와 무관하게 Windows 형식으로 저장하고 대상 runner의 `runtime_env`로 배포한다. 결과·이벤트·manifest·bundle이 같은 변환 함수를 쓴다. |
| P1 4280657538 | project·reference·upstream 경로를 함께 변환하고 setup에서 reference root와 프로젝트 root를 WSL 설정에 넘긴다. |

`RunnerPathFields`에 경로 필드와 값·목록·mapping 변환 방식을 선언했다. 모델 필드의 이름·타입 주석으로 누락을 검사한다. 프롬프트·자유 형식 결과·상대 산출 경로는 보존한다.

## 확인

- PR head `fe83ff8`: 두 P1 및 reference root 회귀 3건 실패 → 수정 후 통과.
- 관련 pytest 297 passed / 27 skipped. 산출물 타입·논문 스킬·라운드 기록 107 passed.
- 새 경로 필드 누락 감지, 원격 gateway의 이벤트, 실제 임시 project·reference 파일 읽기, manifest 원본 보존과 bundle 입력 목록 확인.
- 전체 테스트는 CI에서 실행한다. push 후 결과는 PR checks와 로컬 인계 기록에 남긴다.

## 남은 것

기존 WSL 설정은 정지 후 setup을 다시 실행해야 reference root를 받는다. PI 인스턴스·실제 WSL은 실행하지 않았다. 리뷰 요청·병합은 총괄에게 넘긴다.
