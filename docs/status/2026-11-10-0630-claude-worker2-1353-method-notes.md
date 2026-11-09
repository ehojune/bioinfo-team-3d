# #1353 방법 참고: 채용 없이 정규직 단계에 논문 방법 제공

## 결론
recruit가 꺼진 정규직 연구 단계가 선행 연구 저장본(`inputs/precedent/`)의 방법 절을 `METHOD_NOTES.md`로 받는다. Paper2Skill과 따로 만든 체계가 아니라, #951이 이미 단계에 넣어 주는 hash 확인된 복사본을 그대로 인용한다.

## 바뀐 것
- `labhq/recruit/method_notes.py`(신규): 단계 폴더의 복사본만 읽고 hash를 다시 확인한다. 본문이면 방법 절, 아니면 초록 절을 원문 그대로(논문당 40줄, 전체 12,000자) 옮긴다. 출처·접근 수준·파일·줄을 남기고 초록은 "전문 근거 아님"으로 표시한다. 누락(`not_staged`)·변경(`hash_mismatch`)·절 없음(`no_section`)·길이 초과는 이유와 함께 뺀다.
- `labhq/runner/workspace.py`: 첫 턴에 `METHOD_NOTES.md`를 쓰고 TASK.md에 `## Method notes`를 단다. 실행 기록에 `method_notes`를 남긴다. 이어지는 턴은 다시 쓰지 않는다.
- manual 10(한영)에 한 문단.

## 지키는 것
모델 호출·네트워크·승인 카드·계약·새 파일 접근 0건. 다른 요청의 자료는 이 단계 폴더에 복사본이 없으므로 들어오지 않는다. 논문 속 명령은 인용문이며 실행하지 않는다. 요청 전체 HPC 단계는 저장본을 받지 않으므로(#1336) 방법 참고도 없다.

## 실행한 것
- 새 시험 4건(`tests/test_paper_skill_attach.py`): 본문 방법 절·초록 표시, 누락·변경·외부 자료 제외, recruit 끈 runner 단계 통합, 저장본 없을 때 무변화.
- `test_paper_skill_attach.py`·`test_precedent_texts_951.py` 34건, workspace 시험 54건, TASK.md 관련 4개 파일 249건 통과.

## 미해결
- `paper_skills.py`는 바꾸지 않았다. 승인된 로컬 메서드 카드(Paper2Skill 산출)는 recruit가 켜진 경우의 기존 경로를 그대로 쓴다.
- 절 제목 인식은 규칙 기반이다. 제목이 없는 본문은 `no_section`으로 빠진다.
