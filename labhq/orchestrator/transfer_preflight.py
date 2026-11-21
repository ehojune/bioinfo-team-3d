"""Report the built-in upload limit using metadata from declared PC file references."""

import re
import stat
from collections import Counter
from pathlib import Path

from ..hpc_transfer import Limits
from ..intake import check_reference_path, overlaps_zone, path_pattern, reference_dirs
from ..private_paths import credential_path, resolve_private_paths


def _local_file_size(path):
    # Never follow a link or junction, including one in the path to the file.
    for parent in reversed(path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return None
    info = path.lstat()
    if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        return info.st_size
    return None


def warning(steps, request, settings):
    """Only inspect attached, local file paths named by an HPC step's input_refs."""
    hpc_inputs = {step["id"]: step.get("input_refs", []) for step in steps if step.get("execution") == "hpc"}
    paths = {}
    for raw in reference_dirs(request.get("references")):
        path = Path(raw)
        if path.is_absolute() and path.name and not raw.startswith(("//", "\\\\")) and ".." not in path.parts:
            paths[path] = raw
    if not paths or not any(hpc_inputs.values()):
        return ""
    names = Counter(path.name.casefold() for path in paths)
    private = [Path(path) for path in resolve_private_paths(settings).paths]
    restricted = [settings.path(zone.path) for zone in settings.policy.data_zones if zone.level == "restricted"]
    limit = Limits().upload_max_bytes
    rows = []
    for path, raw in paths.items():
        literal = path_pattern(raw, boundary=True)
        if names[path.name.casefold()] == 1:
            name = path_pattern(path.name, boundary=True)
            literal = rf"(?:{literal}|(?<![\w.-]){name})"
        match = re.compile(literal, re.IGNORECASE)
        users = [sid for sid, inputs in hpc_inputs.items() if any(match.search(value) for value in inputs)]
        if not users:
            continue
        try:
            check_reference_path(raw, settings)
            if credential_path(raw) or overlaps_zone(path, private) or overlaps_zone(path, restricted):
                continue
            size = _local_file_size(path)
        except (OSError, ValueError):
            continue  # Only report sizes this gateway was able to verify.
        if size is not None and size > limit:
            rows.append(f"{path.name} ({', '.join(users)}) · {size:,} byte · {size - limit:,} byte 초과")
    if not rows:
        return ""
    return "\n".join([
        f"PC 사본을 HPC 기본 전송으로 보낼 때 파일당 {limit // 1024} KiB 한도가 적용됩니다.",
        *rows,
        "한도 안으로 나눠 보내거나, 사이트에서 허용하는 방식으로 HPC에서 직접 받도록 계획을 바꾸세요.",
    ])
