"""Deterministic tool-card explanations; these fields never authorize a tool."""
from __future__ import annotations

from .tool_command import commands, read_commands


def tool_input(detail: dict) -> dict:
    import json
    data = detail.get('tool_input', detail.get('input', {}))
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return {}
    return data if isinstance(data, dict) else {}


def explain(detail: dict) -> tuple[str, str, bool]:
    """Action, risk and whether the whole command is covered by templates."""
    from .pi_text import plain_text

    data = tool_input(detail)
    tool = detail.get('tool_name')
    command = str(data.get('command') or '')
    labels = detail.get('path_labels') or {}

    def place(path):
        from .private_paths import inside_any
        for root in sorted(labels, key=len, reverse=True):
            if inside_any(str(path), [root]):
                return plain_text(labels[root])
        return '현재 작업 폴더' if path == '.' else '지정한 경로(자세히에서 확인)'

    if tool in {'Read', 'Glob', 'Grep', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}:
        path = data.get('file_path') or data.get('path') or data.get('notebook_path') or '.'
        reading = tool in {'Read', 'Glob', 'Grep'}
        verb = {'Read': '파일 읽기', 'Glob': '파일 목록 보기', 'Grep': '파일 내용 검색'}.get(tool, '파일 쓰기·수정')
        return f'{place(path)}: {verb}.', ('낮음 · 읽기' if reading else '중간 · 쓰기'), True
    parts = commands(command) if tool in ('Bash', 'PowerShell') else None
    reads = read_commands(command) if tool == 'Bash' else None
    if reads is not None:
        lines, current = [], '현재 작업 폴더'
        names = {'ls': '파일 목록 보기', 'find': '하위 파일 목록 보기', 'cat': '파일 읽기',
                 'head': '앞부분 읽기', 'tail': '끝부분 읽기', 'which': '프로그램 위치 확인', 'pwd': '작업 폴더 위치 확인'}
        for name, paths in reads:
            if name == 'cd':
                current = place(paths[0])
                lines.append(f'{current}로 이동')
            else:
                where = ' · '.join(dict.fromkeys(current if p == '.' else place(p) for p in paths))
                lines.append(f'{where + ": " if where else ""}{names[name]}')
        return '\n'.join(dict.fromkeys(lines)) + '.', '낮음 · 읽기만(쓰기·삭제·네트워크 없음)', True
    if tool == 'PowerShell' and parts and len(parts) == 1:
        words = parts[0]
        name, args = words[0].lower(), words[1:]
        if name == 'new-item' and len(args) == 4 and [a.lower() for a in args[:3]] == ['-itemtype', 'directory', '-force']:
            return f'{plain_text(args[3])} 폴더를 만들려 합니다.', '중간 · 쓰기', True
    # Other literal commands get useful action categories, but never a low-risk verdict.
    actions = []
    complete = bool(parts)
    for words in parts or []:
        name, args = words[0].lower(), words[1:]
        if name in ('ls', 'find', 'cat', 'head', 'tail', 'which', 'pwd', 'cd'):
            actions.append({'ls': '파일 목록 보기', 'find': '파일 검색', 'cat': '파일 읽기',
                            'head': '앞부분 읽기', 'tail': '끝부분 읽기', 'which': '프로그램 위치 확인',
                            'pwd': '작업 폴더 위치 확인', 'cd': '작업 폴더 이동'}[name])
            complete = False  # read grammar rejected flags or syntax; never call this read-only.
        elif name in ('rm', 'rmdir'):
            actions.append('파일·폴더 삭제')
        elif name in ('mkdir', 'touch', 'cp', 'mv'):
            actions.append('파일·폴더 쓰기')
        elif name in ('pip', 'pip3', 'uv', 'npm') and 'install' in args:
            actions.append('패키지 설치(네트워크·쓰기)')
        elif name in ('curl', 'wget', 'ssh', 'scp'):
            actions.append('네트워크 통신(전송 대상·쓰기 여부는 자세히 확인)')
        else:
            complete = False
    if actions:
        locations = list(dict.fromkeys(plain_text(label) for path, label in labels.items() if path in command))
        if locations:
            actions.insert(0, '작업 위치: ' + ' · '.join(locations))
        if not complete:
            actions.append('추가 명령·옵션의 전체 범위는 자세히 확인')
        return '\n'.join(dict.fromkeys(actions)) + '.', '높음 · 변경 또는 외부 통신 가능; 세부 범위 확인 필요', complete
    return '', '미확인 · 스크립트의 읽기·쓰기·삭제·네트워크 범위 확인 필요', False


def reason(detail: dict, policy_reason: str, risk: str) -> str:
    from .pi_text import plain_text

    context = detail.get('task_context') or {}
    if context.get('kind') == 'paper_skill':
        purpose = f"논문 ‘{plain_text(context.get('paper_title') or '제목 미제공')}’으로 이 요청의 자문 스킬을 만드는 중입니다."
    elif context.get('title'):
        purpose = f"작업 목적: {plain_text(context['title'])}."
    else:
        purpose = '작업 목적은 전달되지 않았습니다.'
    if context.get('step_id'):
        purpose += f" 단계: {plain_text(context['step_id'])}."
    rule = '자동 허용 범위 밖이라 실행 전 확인이 필요합니다.'
    if 'private_paths' in policy_reason:
        rule = '걸린 규칙: PI 개인 경로 보호. 개인 파일이나 자격 증명이 있는 경로 접근을 감지했습니다.'
        if risk.startswith('낮음'):
            risk = '중간 · 읽기; 개인 정보 노출 여부 확인 필요'
    elif 'registry' in policy_reason.lower() or '레지스트리' in policy_reason:
        rule = '걸린 규칙: 레지스트리 접근 보호. 계정 정보가 저장된 시스템 설정에 접근합니다.'
    elif '재귀 삭제' in policy_reason or 'recursive' in policy_reason.lower():
        rule = '걸린 규칙: 폴더 안의 파일까지 지우는 재귀 삭제 승인.'
    elif 'restricted' in policy_reason.lower():
        rule = '걸린 규칙: 통제 데이터 경계. 보호된 자료 경로에 접근합니다.'
    elif 'outside' in policy_reason.lower() or 'write' in policy_reason.lower():
        rule = '걸린 규칙: 허용된 작업 폴더 밖 쓰기 확인.'
    if detail.get('permission_mode') == 'auto':
        rule += ' auto여도 이 규칙 때문에 묻습니다.'
    return f'{purpose}\n{rule}\n위험 등급: {risk}.'


def needs_interpretation(detail: dict) -> bool:
    return not explain(detail)[2]
