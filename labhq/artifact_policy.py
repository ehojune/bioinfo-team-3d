"""Paths labhq asks every analysis step to keep, even when they are not declared deliverables."""

INSTRUCTED_OUTPUT_DIRS = (
    "outputs/env/",
    "outputs/scripts/",
    "outputs/reference/",
)
OUTPUT_ENV_DIR, OUTPUT_SCRIPTS_DIR, OUTPUT_REFERENCE_DIR = INSTRUCTED_OUTPUT_DIRS


def is_instructed_output(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix)
               for prefix in INSTRUCTED_OUTPUT_DIRS)
