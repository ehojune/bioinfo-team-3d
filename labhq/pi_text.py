"""Display copy only: never used to grant permission or change the submitted command."""
from __future__ import annotations

import re
import json


PI_TEXT_RULE = """
PI가 읽는 승인 카드 사유·질문·메신저 답·보고 알림은 쉬운 한국어 평문으로 씁니다.
순서는 무엇을 하려는지 → 왜 → 승인하면/끝나면 무슨 결과입니다.
마크다운 제목(#), 굵게(**), 백틱(`), 표와 코드 원문은 쓰지 않습니다. 목록은 줄바꿈으로 나눕니다.
영어 엔진 출력도 뜻을 확인해 한국어로 설명합니다. 명령·코드 원문은 별도 상세 필드나 산출 파일에 둡니다.
모르는 목적·영향 범위·결과는 추측하지 않습니다. 확인한 사실과 직원의 예상은 구분합니다.
JSON 출력 계약의 키·형식은 유지하고 PI에게 보이는 문자열 값에 이 규칙을 적용합니다.
"""


def plain_text(value: object) -> str:
    text = re.sub(r'```[^\n]*\n[\s\S]*?(?:```|$)|~~~[^\n]*\n[\s\S]*?(?:~~~|$)', '', str(value or ''))
    text = re.sub(r'^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)', '', text, flags=re.M)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
    text = text.replace('**', '').replace('__', '').replace('`', '')
    lines = []
    for line in text.splitlines():
        if re.fullmatch(r'[\s|:\-]+', line):
            continue
        lines.append(' · '.join(p.strip() for p in line.strip('|').split('|')) if '|' in line else line)
    return '\n'.join(lines).strip()


def korean_text(value: object, fallback: str) -> str:
    text = plain_text(value)
    return text if re.search('[가-힣]', text) else fallback


def tool_description(detail: dict) -> str:
    """Describe literal operations; compound/opaque scripts retain an explicit scope caveat."""
    data = detail.get('tool_input')
    if not isinstance(data, dict):
        data = detail.get('input') if isinstance(detail.get('input'), dict) else {}
    if not data and isinstance(detail.get('input'), str):
        try:
            parsed = json.loads(detail['input'])
            data = parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            pass
    tool = str(detail.get('tool_name', ''))
    path = data.get('file_path') or data.get('notebook_path') or detail.get('path')
    if path and tool in {'Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}:
        verb = '읽으려' if tool == 'Read' else '수정하려' if tool != 'Write' else '쓰려'
        return f'{plain_text(path)} 파일을 {verb} 합니다.'
    command = str(data.get('command') or '')
    # Only describe a complete, simple literal command. A regex hit inside a script is not its effect.
    if command and not re.search(r'[;|\n\r$`{}()<>]', command):
        words = re.findall(r'"[^"]*"|\x27[^\x27]*\x27|\S+', command)
        words = [w.strip('\"\x27') for w in words]
        name = words[0].lower() if words else ''
        args = words[1:]
        if name == 'new-item' and len(args) == 4 and [a.lower() for a in args[:3]] == ['-itemtype', 'directory', '-force']:
            return f'{plain_text(args[3])} 폴더를 만들려 합니다.'
        if name in {'mkdir', 'get-content', 'cat'} and len(args) == 1 and not args[0].startswith('-'):
            return f'{plain_text(args[0])}' + (' 폴더를 만들려 합니다.' if name == 'mkdir' else ' 파일을 읽으려 합니다.')
        if name in {'remove-item', 'rm'} and len(args) == 2:
            flags = {'-recurse', '-rf', '-r'}
            targets = [w for w in args if w.lower() not in flags]
            if len(targets) == 1 and not targets[0].startswith('-'):
                return f'{plain_text(targets[0])} 폴더와 안의 파일을 지우려 합니다.'
    described = korean_text(data.get('description'), '')
    if described:
        return f'직원 설명: {described}\n실제 명령과 작업 범위는 자세히에서 확인해 주세요.'
    return '직원이 도구를 실행하려 합니다. 여러 명령이나 스크립트의 전체 작업 범위는 자세히에서 확인해 주세요.'


def approval_copy(kind: str, summary: str, detail: dict) -> dict[str, str]:
    """Stable Korean explanation for old and new producers; source fields are left intact."""
    if kind == 'tool_permission':
        from .tool_explanation import explain, reason as tool_reason
        described, risk, _ = explain(detail)
        one = described or tool_description(detail)
        policy_reason = str(detail.get('policy_reason') or summary)
        reason = tool_reason(detail, policy_reason, risk)
        outcome = '승인하면 이 도구 호출을 한 번 허용합니다. 거절하면 실행하지 않습니다.'
    elif kind == 'hpc_submit':
        one = '계산 서버에 작업을 보내려 합니다.'
        reason = korean_text(detail.get('reason'), '서버 자원을 쓰는 작업이라 실행 전에 확인이 필요합니다.')
        if detail.get('cores') and detail.get('walltime'):
            one += f" 처리 장치 {detail['cores']}개를 최대 {detail['walltime']} 동안 요청합니다."
        outcome = '승인하면 작업을 제출합니다. 결과는 서버 작업이 끝난 뒤 확인합니다.'
    elif kind == 'budget':
        one = '작업을 계속하기 위해 비용 상한을 확인하려 합니다.'
        reason = '지금 상한으로는 다음 작업을 보내기 어렵거나 아직 집계되지 않은 비용이 있습니다.'
        spent, limit = detail.get('spent_usd'), detail.get('limit_usd')
        if isinstance(spent, (int, float)) and isinstance(limit, (int, float)):
            reason = f'지금까지 ${spent:.2f} 썼고 현재 상한은 ${limit:.2f}입니다.'
            if detail.get('unknown_count'):
                reason += f" 아직 비용을 모르는 작업이 {detail['unknown_count']}건 있어 추가 비용을 고려해야 합니다."
            else:
                reason += ' 다음 작업을 보내려면 상한을 높여야 합니다.'
        amount = detail.get('requested_budget_usd')
        outcome = (f'승인하면 비용 상한을 ${amount:.2f}로 올립니다.' if isinstance(amount, (int, float)) else
                   '승인하면 카드에 제시된 비용 범위로 작업을 이어 갑니다.') + ' 거절하면 새 작업을 보내지 않습니다.'
    elif kind == 'recruit':
        one = '필요한 일을 맡길 직원을 추가하려 합니다.'
        reason = korean_text(detail.get('reason'), '현재 직원만으로 맡기기 어려운 일이 있습니다.')
        outcome = '승인하면 제안된 직원의 준비를 시작합니다. 준비가 끝나야 일을 맡깁니다.'
    elif kind == 'pipeline_contribute':
        one = f"완성한 {plain_text(detail.get('name'))} pipeline을 bioinfo-agent에 기여할까요?"
        reason = '새 pipeline의 코드를 공개 저장소에 보내기 전에 건별 동의를 받습니다.'
        outcome = '승인하면 이 후보의 코드로 PR을 엽니다. 거절하면 기여하지 않고 분석 결과를 보관합니다.'
    elif kind == 'disk_space':
        one = '작업 폴더가 있는 드라이브의 여유 공간이 거의 없습니다.'
        free, floor = detail.get('free_gib'), detail.get('floor_gib')
        reason = (f'여유가 {free:.1f} GiB이고 시작 기준은 {floor:g} GiB입니다. 디스크가 차면 이 작업과 gateway가 멈출 수 있습니다.'
                  if isinstance(free, (int, float)) and isinstance(floor, (int, float)) else
                  '디스크가 차면 이 작업과 gateway가 멈출 수 있습니다.')
        outcome = '승인하면 이 작업을 그대로 시작합니다. 거절하거나 시간이 끝나면 시작하지 않고 이유를 남깁니다.'
    elif kind == 'wsl_setup':
        one = 'WSL 준비가 덜 됐어요. 셋업할까요?'
        missing = detail.get('missing') if isinstance(detail.get('missing'), list) else []
        reason = ('bioinfo-agent는 이 PC의 WSL에서 돌아가는데 아직 준비되지 않았습니다.'
                  + (f" 빠진 것: {', '.join(plain_text(item) for item in missing[:8])}." if missing else ''))
        outcome = ('승인하면 관리자 권한이 필요 없는 준비(Nextflow 설치, WSL 안 labhq, runner 등록)를 바로 합니다. '
                   '관리자 명령이 남으면 카드가 보여 주고, 요청은 기다리지 않고 멈춥니다. 거절하면 셋업하지 않습니다.')
    elif kind == 'literature_pdf':
        papers = detail.get('papers') if isinstance(detail.get('papers'), list) else []
        one = f'핵심 논문 {len(papers)}편의 PDF를 요청합니다.'
        reason = '허용된 공개·기관 경로에서 결론에 필요한 본문을 읽지 못했습니다.'
        outcome = ('지정 파일에 PDF를 넣고 승인하면 본문으로 이어 갑니다. 거절하거나 대기 시간이 끝나면 '
                   '초록 또는 다른 오픈 액세스 논문으로 진행하고 한계에 남깁니다.')
    else:
        one = korean_text(summary, '다음 작업을 진행하기 전에 확인이 필요합니다.')
        reason = korean_text(detail.get('why_blocked') or detail.get('reason'), '진행 방향을 정하려면 PI의 답이 필요합니다.')
        outcome = '선택한 답을 직원에게 전달합니다.'
    return dict(pi_summary=one, pi_reason=reason, pi_outcome=outcome)


def with_approval_copy(card: dict) -> dict:
    from .tool_explanation import needs_interpretation
    copy = approval_copy(str(card.get('kind', '')), str(card.get('summary', '')), card.get('detail') or {})
    return {**card, **{key: card.get(key) or value for key, value in copy.items()},
            **({'pi_explainer': 'chief_of_staff', 'pi_needs_interpretation':
                card.get('kind') != 'tool_permission' or needs_interpretation(card.get('detail') or {})}
               if not card.get('pi_summary') else {})}
