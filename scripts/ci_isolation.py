"""Pure controller/worker isolation checks; never discover operator credentials."""
from pathlib import Path


def operator_home(original: str, workerinput: dict | None = None) -> str:
    if workerinput is not None:
        supplied = workerinput.get("labhq_operator_home")
        if not supplied or Path(supplied).resolve() != Path(original).resolve():
            raise ValueError("xdist worker lost the original operator home")
    return original
