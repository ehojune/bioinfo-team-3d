"""Local LLM-wiki reference with provenance (#827): a read-only folder, an incremental index, no writes."""


def cli_main(settings, action, target, parser) -> int:
    """`labhq wiki connect|status|reindex|search|disconnect`; local to this PC, no gateway needed."""
    from . import index, walker

    def need_connected() -> None:
        if not (settings.wiki.enabled and settings.wiki.root):
            parser.exit(2, "labhq: wiki가 꺼져 있습니다. 먼저 `labhq wiki connect <폴더>`로 확인하고 설정에 wiki 블록을 넣으세요.\n")

    try:
        if action == "connect":
            if not target:
                parser.exit(2, "labhq: connect에는 wiki 폴더가 필요합니다.\n")
            probe = settings.model_copy(update={"wiki": settings.wiki.model_copy(
                update={"enabled": True, "root": target})})
            files, skipped = walker.list_files(probe)
            print(f"연결할 수 있습니다: {target}\n  색인 대상 {len(files)}개"
                  + (f", 건너뜀 {dict(skipped)}" if skipped else ""))
            print("설정 파일에 다음을 넣은 뒤 `labhq wiki reindex`를 실행하세요(wiki 폴더는 읽기만 합니다):\n"
                  f"wiki:\n  enabled: true\n  root: {target}\n  external_model_allowed: false")
        elif action == "status":
            info = index.status(settings)
            print(f"wiki: {'켜짐' if info['enabled'] else '꺼짐'} root={info['root'] or '-'}")
            print(f"  색인 {'있음' if info['available'] else '없음'}: 문서 {info['docs']}, 조각 {info['chunks']}, "
                  f"출처 {info['origins']}, 오래됨 {info['stale']}"
                  + (f", 마지막 갱신 {int(__import__('time').time() - info['last_reindex'])}초 전"
                     if info['last_reindex'] else ""))
            if info["skipped"]:
                print(f"  건너뜀: {info['skipped']}")
            if info["root_problem"]:
                print(f"  연결 문제: {info['root_problem']}")
        elif action == "reindex":
            need_connected()
            stats = index.reindex(settings)
            print(f"색인 갱신: 파일 {stats['files']}개 — 추가 {stats['added']}, 수정 {stats['updated']}, "
                  f"시각만 변경 {stats['touched']}, 그대로 {stats['unchanged']}, 삭제 {stats['removed']}"
                  + (f", 읽지 못함 {stats['unreadable']}" if stats["unreadable"] else "")
                  + (f", 건너뜀 {stats['skipped']}" if stats["skipped"] else ""))
        elif action == "search":
            need_connected()
            if not target:
                parser.exit(2, "labhq: search에는 검색어가 필요합니다.\n")
            for hit in index.search(settings, target):
                origin = next(iter(hit["origin"].items()), None) if hit["origin"] else None
                print(f"{hit['rel_path']}:{hit['line_start']}-{hit['line_end']} [{hit['freshness']}] "
                      f"{hit['title']} · {hit['heading'] or '-'} · sha256 {hit['sha256'][:12]}"
                      + (f" · {origin[0]}:{origin[1]}" if origin else "") + f"\n  {hit['snippet'][:160]!r}")
        elif action == "disconnect":
            print("색인을 지웠습니다. wiki 폴더는 건드리지 않았습니다. 설정의 wiki 블록은 직접 지우거나 enabled: false로 두세요."
                  if index.disconnect(settings) else "지울 색인이 없습니다.")
    except ValueError as error:
        parser.exit(2, f"labhq: wiki: {error}\n")
    return 0
