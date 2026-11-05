## 결론

로컬로 끝난 요청을 나중에 GitHub 저장소로 옮기는 `labhq storage <요청 id> --to github`를 더했습니다. 저장 위치 카드는 PI 문구인 "결과를 GitHub 저장소로 정리할까요?"로 묻고, 토큰이 없거나 권한이 모자라면 무엇이 빠졌는지와 나중에 올리는 법을 보입니다. Refs #688.

## 바뀐 것

- `ResultStorage.to_github`, API `POST /api/requests/<id>/storage/to-github`, CLI `--to github [--name] [--visibility]`. 로컬 정리본이 있는 요청만 옮깁니다. 토큰은 `GET /user`와 이름 없는 `POST /user/repos`(422면 만들기 권한 있음, 아무것도 만들지 않음, #687 실측)로 먼저 확인합니다. 그다음 새 저장소로 바꾸고, 저장소 첫 화면 구성으로 편집자가 정리본을 다시 만듭니다. 게시 카드 승인과 보고서 검토 조건은 그대로라 승인 전에는 아무것도 올리지 않습니다.
- 저장 위치 카드: 질문 문구, 그리고 토큰 없음·권한 확인 실패 때의 안내(`labhq init`, 필요한 권한, `--to github`).
- fine-grained 토큰이면 "Repository access는 All repositories" 안내를 답에 붙입니다.

## 확인

- 새 test 14개(성공 흐름, 검토 대기, 거절 네 가지, 이름·공개 범위, 토큰 없음, 401·403, 통제 데이터, fine-grained 안내, 카드 문구, CLI). 저장·게시·정리본 관련 test 487 passed, 웹 카드 Node test 3개 통과.

## 남은 것 — PI 결정 필요

- 지금 저장 위치 카드는 요청이 **끝날 때**가 아니라 **계획 단계**(CP1 전, 확인 질문과 함께)에 뜹니다. PI는 끝날 때 묻는다고 말했습니다. 시점을 옮기려면 `cso.py`를 고쳐야 해서 #686 2단계 레인이 병합된 뒤 합니다. 옮길지는 #688에서 여쭙니다.
