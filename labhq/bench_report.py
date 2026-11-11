"""Keep the benchmark result block at the end of a report when it is unambiguous."""

import re


_RESULT_BLOCK = re.compile(r"<!-- LABHQ_BENCH_RESULT -->.*?<!-- /LABHQ_BENCH_RESULT -->", re.DOTALL)


def split_trailing_result(report: str) -> tuple[str, str]:
    """Split one trailing block; leave non-trailing or multiple blocks untouched."""
    blocks = list(_RESULT_BLOCK.finditer(report))
    if len(blocks) == 1 and not report[blocks[0].end():].strip():
        return report[:blocks[0].start()].rstrip(), blocks[0].group(0)
    return report, ""


def restore_trailing_result(report: str, benchmark: str) -> str:
    return report + (("\n\n" + benchmark) if benchmark else "")
