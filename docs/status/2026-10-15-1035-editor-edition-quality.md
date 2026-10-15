## 결론

Refs #607. 시운전 정리본에서 확인된 편집자 문제 다섯 가지를 고쳤습니다. 출처가 비지 않고, 편집자가 보고서 전문을 받고, README 링크 줄은 한 번만 나오며, 게시 카드가 떠 있는 정리본을 다시 만들 수 있고, 끝난 요청의 낡은 재개 카드가 지워집니다.

## 바뀐 것

- **출처** (`labhq/edition_sources.py`, 새 파일): `SOURCES.md`가 보고서·부록·정리본에 들어간 `resource_versions` 파일에서 URL, DOI(`https://doi.org/…`), PMID·PMC, GEO(GSE·GSM·GPL), SRA(SRR·SRP·SRX·SRS), ArrayExpress, BioProject 번호를 찾아 링크로 적습니다. 중복(`doi:` 글자와 `doi.org` 링크, PMID와 PubMed 링크)은 한 줄로 합치고 종류별로 묶습니다. `resource_versions` 표의 자료·버전·출처 링크는 "소프트웨어·자료 버전"으로 적습니다. 로컬·내부 주소와 토큰이 든 URL 쿼리는 뺍니다. 아무것도 못 찾았을 때만 "출처를 찾지 못했습니다"를 씁니다.
- **편집자 입력** (`result_storage.editor_prompt`): README 초안(보고서 앞 1800자)이 아니라 보고서·진행 기록·재현 방법·출처를 전문으로 보냅니다. 문서가 한도(보고서 10만 자, 나머지 3만 자)를 넘을 때만 개요와 온전한 절을 보내고, 어느 절이 본문 없이 갔는지 적고 "기록이 불완전하다고 쓰지 말라"고 알립니다. 편집자 지시문(`agents/core/editor.yaml`)에도 같은 줄을 넣었습니다. 편집자 없이 만드는 기본 README는 보고서 앞부분을 문단·문장 끝에서 자르고 "report.md에 있습니다"를 붙입니다.
- **링크 줄**: README의 문서 링크 줄은 편집자가 쓴 것이 있으면 그대로 두고, 빠진 링크만 한 줄로 더합니다.
- **다시 만들기**: `labhq storage <요청 id> --redraft`, `POST /api/requests/{id}/storage/redraft`. 게시 카드가 떠 있거나 PI가 거절한 정리본의 편집자를 다시 돌려 이전 카드를 닫고 새 카드를 엽니다. 정리본 폴더는 새로 만들어 이전 파일이 남지 않습니다. 이미 게시한 정리본, 정리본이 없는 요청, 안 끝난 요청은 이유와 함께 409로 거부합니다.
- **낡은 재개 카드** (`labhq/gateway/server.py`): 요청이 끝날 때, PI가 검토 완료를 표시할 때, 게이트웨이를 시작할 때 그 요청의 재개 카드를 지우고 `approval.expired` 이벤트와 `approval_decision` 기록을 남깁니다. 낡은 재개 카드에 답해도 끝난 요청을 다시 돌리거나 실패로 바꾸지 않습니다.
- `docs/manual.md` 편집자 절.

## 확인

- 새 test 19건(`tests/test_edition_quality_607.py` 14건, `tests/test_resume_cards_607.py` 5건)이 main 코드에서 모두 실패하고 이 branch에서 통과합니다. mock editor·mock GitHub만 씁니다. 출처 fixture는 시운전 보고서 모양(GSE132465·GSE144735·GSM3868436, PMID 25706627·25706628과 DOI)입니다.
- 관련 Python test, Node test 전부, `scripts/patch_notes.py check`, `scripts/check_public.sh` 통과.

## 남은 것

- 편집자가 입력에 없는 말을 지어내 "끊겼다"고 쓰는 일 자체는 막지 못합니다. 입력과 지시문에서 이유를 없앤 것까지입니다.
- 시운전 정리본(`req_75b2fd4b0c`)은 이 PR이 병합된 뒤 `labhq storage req_75b2fd4b0c --redraft`로 다시 만듭니다.
