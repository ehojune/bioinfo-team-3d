# HPC 단계 workspace (#578)

구현·mock 검증 기록. 실제 HPC·계정 전환·OS 격리는 UNVERIFIED다.

| 경로 | 보관하는 것 |
|---|---|
| `hpc/<request>/steps/<step>/` | 단계별 manifest·jobs·outputs·wake run |
| `hpc/<request>/data/<producer>/<source-task>/` | 승인된 조상 산출의 hash 검증 복사본 |
| `hpc/<request>/.labhq/hpc-inputs/<producer>/<source-task>.json` | 생산 단계·원래 경로·hash |
| `requests/<request>/` | 원본과 분리된 요청 묶음 |

- 서로 다른 단계의 동명 산출은 허용한다. 같은 단계의 required·optional 파일은 equality·ancestor·대소문자 충돌을 거부한다.
- 새 단계는 기존 요청 공유 폴더를 채택하지 않는다. 구형 원본·잡 기록은 보존한다.
- 앞 단계 입력은 기존 `stage_inputs`의 요청·task·step·성공·hash·용량·통제/개인 경로 검사를 먼저 거친다. 거부하면 원본 경로로 우회하지 않는다. 전달한 파일만 요청 data에 복사하며 task별 버전은 서로 덮지 않는다.
- 공유 계정은 data 복사본만 읽고 단계 outputs에 쓴다. 입력 symlink·hardlink 거부는 유지한다. 운영자가 workspace 위쪽 경로의 traverse 권한을 준비한다.
- 잡 제출 전 baseline을 run에 저장한다. 같은 요청·step·revision의 wake는 첫 제출 baseline을 이어받아 대기 중 생긴 보조 파일도 기록한다. revision이 바뀌면 새 baseline을 쓴다.
- 재개 accounting은 session·runner·engine에 요청·step·revision을 함께 대조한다. verify는 절대 경로가 사라졌을 때 task가 일치하는 유일한 중첩 manifest만 찾는다.
- bundle은 해당 run의 engine/model을 우선한다. 단계 절대 링크와 기록된 data snapshot 링크를 실제 묶음 파일로 바꾼다. model은 RO-Crate software identity hash에 반영된다.

## 04장 후속 반영

rev2는 #1276 소유의 `04-install.md`·`.en.md`를 편집하지 않는다. main의 장 분리·목차를 보존했다.
이전 PR의 설치 절에 있던 실행 위치 질문·direct 대상 제한·선택 기록·HPC/local 표를 후속 claim에서 04장에 옮긴다.
그때 공유 `outputs/` 설명은 위 단계별 경로·data 복사로 바꾸고 실제 HPC 미검증을 명시한다.

## 회귀 범위

CLI `1: a/b`, 실제 worker만 HPC 선택, 동명 산출·파일 경로 충돌, mock 제출·입력 provenance, wake·runner 재시작·revision 분리, 구형 원본 보존, bundle 링크·run identity, 이동 후 verify·오귀속 거부를 검사한다.

rev2 로컬: 확장 993 passed/39 skipped, 후속 bundle·accounting·CSO 413/7, 경계 138/4(서로 중복 포함). Windows의 POSIX 권한·링크 검사는 skip이며 Linux CI가 맡는다.
08·11장 한영은 합계 158,935→160,636자(+1,701). 경로·검증 상세는 이 reference에 뒀다.
