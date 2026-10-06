"""whoami and icacls are read in the console code page, so Python UTF-8 mode on Korean Windows still parses them."""

import os
import subprocess
from pathlib import Path

import pytest

from labhq.tools import annot_keys


@pytest.mark.skipif(os.name != "nt", reason="whoami and icacls run only on Windows")
def test_console_tools_decode_in_the_console_code_page_not_utf8(monkeypatch):
    seen = []

    def fake_run(args, **kwargs):
        seen.append((args[0], kwargs.get("encoding"), kwargs.get("errors")))
        # CP949 for "처리된 파일: 1": UTF-8 mode with plain text=True could not decode this and left stdout None.
        out = '"PC\\me","S-1-5-21-1"\n' if args[0] == "whoami" else "C:\\k PC\\me:(F)\n\n" + "처리된 파일: 1\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(annot_keys.subprocess, "run", fake_run)
    assert annot_keys.current_account() == ("PC\\me", "S-1-5-21-1")
    assert annot_keys.acl_entries(Path("C:\\k")) == [("PC\\me", "(F)")]
    assert seen == [("whoami", "oem", "replace"), ("icacls", "oem", "replace")]
