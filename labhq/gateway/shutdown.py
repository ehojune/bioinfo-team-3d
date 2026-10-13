"""PI-only web shutdown, using the same identity checks and stop path as `down`."""
from __future__ import annotations

import os

from fastapi import HTTPException
from pydantic import BaseModel, StrictBool

from .. import cli


class ShutdownIn(BaseModel):
    force: StrictBool = False
    confirmation: str = ""


class WebShutdown:
    def __init__(self, settings, hub):
        self.settings = settings
        self.hub = hub
        self.status = "idle"

    def prepare(self, body: ShutdownIn) -> dict:
        if self.status == "stopping":
            raise HTTPException(409, "이미 종료 중입니다.")
        if body.force and body.confirmation != "interrupt-running-work":
            raise HTTPException(422, "진행 중인 일이 중단된다는 경고를 확인한 뒤 다시 눌러 주세요.")
        readiness = self.hub.shutdown_readiness()
        if not readiness["ready"] and not body.force:
            raise HTTPException(409, {"message": "진행 중인 일이 있어 종료하지 않았습니다.",
                                      "readiness": readiness})
        # Never use records belonging to another gateway/config, even if the token matches.
        for role in ("gateway", "runner"):
            state, record, _ = cli._process_record(self.settings, role, cleanup=False)
            if state != "alive" or (role == "gateway" and record["pid"] != os.getpid()):
                raise HTTPException(409, "이 인스턴스의 PID 기록을 확인할 수 없어 끌 수 없습니다. "
                                    "labhq up으로 시작한 인스턴스에서 사용하세요.")
        self.status = "stopping"
        return {"status": self.status}

    def run(self, force: bool) -> None:
        # Starlette runs this synchronous background task off the event loop, AFTER sending the response.
        # down acquires the up/down lock and checks readiness again before stopping runner then gateway.
        try:
            cli._down(self.settings, force=force)
        except (SystemExit, Exception):
            self.status = "failed"
        else:
            # A real successful down terminates this process. Reaching here means it is still answering.
            self.status = "failed"
