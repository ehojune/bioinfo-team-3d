from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .settings import Settings
from .util import free_port, short

REPO = Path(__file__).resolve().parents[1]
ICON = {"cso": "🦉", "chief_of_staff": "🐧", "biologist": "🐻", "data_steward": "🐿️", "lit_scout": "🦊",
        "analyst": "🦝", "engineer": "🐙", "qc_reviewer": "🦔", "sci_reviewer": "🐢", "recruiter": "🦫", "bioinfo-agent": "🦦"}
STATE_KO = {"working": "작업 중", "waiting": "승인 대기", "hibernating": "HPC 대기(수면)", "done": "완료", "error": "오류"}


def render(ev: dict) -> None:
    t, a, d = ev.get("type"), ev.get("agent_id") or "", ev.get("data") or {}
    ic = ICON.get(a, "🐥" if a.startswith("c_") else "·")
    ts = datetime.fromtimestamp(ev.get("ts", time.time())).strftime("%H:%M:%S")
    line = None
    if t == "agent.status" and d.get("state") in STATE_KO and not (d["state"] == "working" and "task" not in d
                                                                    and "approval" not in d and d.get("model")):
        extra = f" — {d['task']}" if d.get("task") else ""
        extra += f" (jobs: {', '.join(d['jobs'])})" if d.get("jobs") else ""
        extra += f" ⚠ {d['error']}" if d.get("error") else ""
        line = f"{ic} {a:<15}{STATE_KO[d['state']]}{extra}"
    elif t == "agent.tool":
        line = f"{ic} {a:<15}🔧 {d.get('name')} {short(d.get('input'), 80)}"
    elif t == "approval.requested":
        line = f"📱 승인 요청 [{d.get('kind')}] {d.get('summary')}  (id={d.get('id')})"
    elif t == "approval.resolved":
        line = f"✅ 승인 결과: {'허가' if d.get('approved') else '거절'} {d.get('note', '')}"
    elif t == "job.submitted":
        line = f"🖥️  HPC 제출 {d.get('job_id')} ({d.get('name')})"
    elif t == "job.state":
        line = f"🖥️  {d.get('job_id')} → {d.get('state')} (exit={d.get('exit_status')})"
    elif t == "jobs.finished":
        line = f"⏰ HPC 작업 종료 → {ic} {a} 깨우기"
    elif t == "request.plan":
        rows = [f"   {s['id']} → {ICON.get(s['agent_id'], '·')} {s['agent_id']:<13} deps={s['depends_on']}  {s['instruction']}"
                for s in d.get("steps", [])]
        line = "📋 CSO 계획\n" + "\n".join(rows) + "".join(f"\n   ⚠ {w}" for w in d.get("warnings", []))
    elif t == "recruit.suggested":
        line = f"🧾 CSO 채용 제안: {d.get('repo') or d.get('paper')} — {d.get('reason')}"
    elif t == "request.review":
        line = f"🐢 과학 리뷰 #{d.get('revision')}: {d.get('verdict')} {d.get('scores')}"
        line += "".join(f"\n   - {i.get('step_id')}: {i.get('problem')} → {i.get('request')}" for i in d.get("issues") or [])
    elif t == "recruit.status":
        line = f"🦫 인사팀: {d.get('slug')} {d.get('stage')}"
    elif t == "recruit.done":
        ag = d.get("agent", {})
        line = f"🐥 파견직 입사: {ag.get('id')} ({ag.get('name')}) · 수습통과={d.get('passed_probation')}"
    elif t == "recruit.failed":
        line = f"🦫 채용 실패: {d.get('error')}"
    elif t == "request.completed":
        line = f"🏁 요청 완료 (ok={d.get('ok')}, cost=${d.get('cost_usd')})"
    elif t == "request.failed":
        line = f"💥 요청 실패: {d.get('error')}"
    elif t in ("runner.online", "runner.offline"):
        line = f"🔌 {t}: {d.get('runner_id')}"
    if line:
        print(f"[{ts}] {line}", flush=True)


# ---------------- HTTP / WS client helpers ----------------
def _http_base(s: Settings) -> str:
    return s.gateway.url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


def _api(s: Settings, method: str, path: str, **kw):
    import httpx

    r = httpx.request(method, _http_base(s) + path, headers={"Authorization": f"Bearer {s.gateway.client_token}"},
                      timeout=30, **kw)
    r.raise_for_status()
    return r.json()


async def _watch(s: Settings, request_id: str | None = None) -> None:
    import websockets

    url = f"{s.gateway.url.rstrip('/')}/ws/client?token={s.gateway.client_token}"
    async with websockets.connect(url, max_size=64 * 2**20) as ws:
        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("type") == "snapshot":
                continue
            if request_id and ev.get("request_id") not in (request_id, None):
                continue
            render(ev)
            if request_id and ev.get("request_id") == request_id and ev["type"] in ("request.completed", "request.failed"):
                print("\n" + (ev["data"].get("report") or ev["data"].get("error") or ""))
                return


async def _send_and_wait(s: Settings, body: dict) -> None:
    import websockets

    url = f"{s.gateway.url.rstrip('/')}/ws/client?token={s.gateway.client_token}"
    async with websockets.connect(url, max_size=64 * 2**20) as ws:
        await ws.recv()  # snapshot
        rid = _api(s, "POST", "/api/requests", json=body)["request_id"]
        print(f"request {rid}")
        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("request_id") != rid:
                continue
            render(ev)
            if ev["type"] in ("request.completed", "request.failed"):
                print("\n" + (ev["data"].get("report") or ev["data"].get("error") or ""))
                return


# ---------------- demo (no API keys, no cluster) ----------------
async def _demo(web: bool = False, port: int = 8787) -> None:
    import uvicorn

    from .gateway.server import RequestIn, create_app
    from .runner.daemon import Runner

    tmp = Path(tempfile.mkdtemp(prefix="labhq-demo-"))
    shutil.copytree(REPO / "agents", tmp / "agents")
    s = Settings.load(None)
    gport = port if web else free_port()
    s.gateway.port, s.gateway.url = gport, f"ws://127.0.0.1:{gport}"
    s.runner.broker_port, s.runner.force_engine, s.runner.job_poll_s = free_port(), "mock", 1
    s.runner.workspace_root, s.runner.talent_dir, s.runner.agents_dir = str(tmp / "runs"), str(tmp / "talent"), str(tmp / "agents")
    s.hpc.scheduler = "mock"

    app = create_app(s)
    hub = app.state.hub
    publish = hub.publish

    async def auto_approve(aid: str) -> None:
        await asyncio.sleep(0.3)
        print("   (demo) 📱 폰에서 '승인' 탭했다고 가정")
        await hub.resolve_approval(aid, True, "demo auto-approve")

    async def tap(ev: dict) -> None:
        await publish(ev)
        render(ev)
        if ev.get("type") == "approval.requested":
            asyncio.get_running_loop().create_task(auto_approve(ev["data"]["id"]))

    hub.publish = tap
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=gport, log_level="warning"))
    tasks = [asyncio.create_task(server.serve())]
    while not server.started:
        await asyncio.sleep(0.05)
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))

    async def until(pred, timeout: float) -> None:
        t0 = time.time()
        while not pred():
            if time.time() - t0 > timeout:
                raise TimeoutError("demo step timed out")
            await asyncio.sleep(0.1)

    try:
        await until(lambda: len(hub.agents) >= 5, 15)
        if web:
            print(f"\n=== 웹 사무실: http://127.0.0.1:{gport}/?token={s.gateway.client_token} (Ctrl+C로 종료) ===\n")
            texts = ["공개 폐선암 scRNA-seq에서 CD276 고발현 세포유형 찾고 QC까지 [hpc] [needs-approval] [revise] [recruit]",
                     "새로 받은 WGS 배치 표준 QC [hpc] [needs-approval]"]
            for i in range(10**6):
                rid = hub.create_request(RequestIn(text=texts[i % 2]))
                await until(lambda: hub.requests[rid]["status"] != "running", 120)
                if i == 0 and "c_scanpy" not in hub.agents:
                    await asyncio.sleep(3)
                    await hub.send_runner(s.runner.id, {"type": "recruit.start", "repo": "https://github.com/scverse/scanpy",
                                                        "focus": "Preprocessing and clustering", "ttl_days": 14,
                                                        "request_id": rid})
                await asyncio.sleep(8)
        print(f"\n=== 정규직 {len(hub.agents)}명 출근 · 오케스트레이션 요청 ===\n")
        rid = hub.create_request(RequestIn(
            text="공개 폐선암 scRNA-seq에서 CD276(B7-H3) 고발현 세포유형 찾고 QC까지 [hpc] [needs-approval] [revise] [recruit]"))
        await until(lambda: hub.requests[rid]["status"] != "running", 60)
        print("\n--- CSO 최종 보고 ---\n" + (hub.requests[rid].get("report") or ""))

        print("\n=== CSO 채용 제안을 PI가 승인 → 파견직 채용 (Paper2Agent) ===\n")
        await hub.send_runner(s.runner.id, {"type": "recruit.start", "repo": "https://github.com/scverse/scanpy",
                                            "paper": "https://doi.org/10.1186/s13059-017-1382-0",
                                            "focus": "Preprocessing and clustering", "ttl_days": 14})
        await until(lambda: "c_scanpy" in hub.agents, 30)
        rid2 = hub.create_request(RequestIn(text="scanpy 논문 방식으로 PBMC 전처리·클러스터링 계획 자문",
                                            mode="direct", agent_id="c_scanpy"))
        await until(lambda: hub.requests[rid2]["status"] != "running", 30)
        print("\n--- 파견직 응답 ---\n" + (hub.requests[rid2].get("report") or ""))
        print(f"\n작업 폴더(실험노트): {tmp / 'runs'}\n인재풀: {tmp / 'talent'}")
    finally:
        runner.stop()
        server.should_exit = True
        await asyncio.sleep(0.3)
        for t in tasks:
            t.cancel()


# ---------------- entry point ----------------
def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="labhq", description="Bio lab HQ — multi-agent research lab")
    p.add_argument("-c", "--config", default=None, help="config YAML (default: $LABHQ_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gateway")
    sub.add_parser("runner")
    sub.add_parser("agents")
    sp = sub.add_parser("send")
    sp.add_argument("text")
    sp.add_argument("--agent", help="direct mode: send to one agent")
    sp.add_argument("--project-dir", action="append", default=[])
    sp.add_argument("--budget", type=float)
    sp.add_argument("--project", help="project id → updates go to that project's GitHub repo")
    sp.add_argument("--no-wait", action="store_true")
    sub.add_parser("watch")
    sub.add_parser("projects", help="list projects and their GitHub repos")
    cr = sub.add_parser("codex-review", help="ask Codex to review a PR in a project repo (posts '@codex review')")
    cr.add_argument("project")
    cr.add_argument("pr", type=int)
    cr.add_argument("--note", default="")
    sub.add_parser("approvals")
    ap = sub.add_parser("approve")
    ap.add_argument("id")
    ap.add_argument("--deny", action="store_true")
    ap.add_argument("--note", default="")
    rp = sub.add_parser("recruit", help="hire a contract agent from a paper/repo via Paper2Agent")
    rp.add_argument("--paper")
    rp.add_argument("--repo")
    rp.add_argument("--focus")
    rp.add_argument("--ttl", type=float, help="contract length in days")
    rp.add_argument("--name")
    cp = sub.add_parser("contract", help="extend | release | activate | rehire a contract agent")
    cp.add_argument("action", choices=["extend", "release", "activate", "rehire"])
    cp.add_argument("target", help="agent id (or talent slug for rehire)")
    cp.add_argument("--days", type=float)
    sub.add_parser("talent", help="list the talent pool (past contract agents)")
    sub.add_parser("setup-paper2agent", help="install the paper2agent skill for Claude Code and Codex")
    dp = sub.add_parser("demo", help="offline demo with mock agents (no API keys, no cluster)")
    dp.add_argument("--web", action="store_true", help="keep running and serve the web office")
    dp.add_argument("--port", type=int, default=8787)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = Settings.load(args.config)

    if args.cmd == "gateway":
        import uvicorn

        from .gateway.server import create_app

        uvicorn.run(create_app(s), host=s.gateway.host, port=s.gateway.port, log_level="info")
    elif args.cmd == "runner":
        from .runner.daemon import Runner

        asyncio.run(Runner(s).run_forever())
    elif args.cmd == "agents":
        for a in _api(s, "GET", "/api/agents"):
            kind = f"파견 ~{datetime.fromtimestamp(a['expires_at']):%m-%d}" if a.get("expires_at") else "정규"
            print(f"{ICON.get(a['id'], '🐥')} {a['id']:<16} {a['name']:<18} {a['engine']:<11} {a.get('model') or '-':<8} {kind}")
    elif args.cmd == "send":
        body = {"text": args.text, "mode": "direct" if args.agent else "orchestrate", "agent_id": args.agent,
                "project_dirs": args.project_dir, "budget_usd": args.budget, "project_id": args.project}
        if args.no_wait:
            print(_api(s, "POST", "/api/requests", json=body))
        else:
            asyncio.run(_send_and_wait(s, body))
    elif args.cmd == "watch":
        asyncio.run(_watch(s))
    elif args.cmd == "projects":
        for pr in _api(s, "GET", "/api/projects"):
            print(f"{pr['id']:<16} {pr.get('repo') or '-':<32} {pr['visibility']:<8} "
                  f"issues={pr['issues']} reports={pr['commit_reports']}")
    elif args.cmd == "codex-review":
        print(_api(s, "POST", f"/api/projects/{args.project}/prs/{args.pr}/codex-review", json={"note": args.note}))
    elif args.cmd == "approvals":
        for a in _api(s, "GET", "/api/approvals"):
            print(f"{a['id']}  [{a['kind']}] {a['summary']}")
    elif args.cmd == "approve":
        print(_api(s, "POST", f"/api/approvals/{args.id}", json={"approved": not args.deny, "note": args.note}))
    elif args.cmd == "recruit":
        print(_api(s, "POST", "/api/recruit", json={"paper": args.paper, "repo": args.repo, "focus": args.focus,
                                                    "ttl_days": args.ttl, "name": args.name}))
    elif args.cmd == "contract":
        body = {"action": args.action, "days": args.days, "slug": args.target if args.action == "rehire" else None}
        print(_api(s, "POST", f"/api/contracts/{args.target}", json=body))
    elif args.cmd == "talent":
        from .registry import Registry

        reg = Registry(s.path(s.runner.agents_dir), s.path(s.runner.talent_dir))
        for spec in reg.talent_pool():
            c = spec.contract
            print(f"🐥 {Path(c.talent_dir).name if c and c.talent_dir else spec.id:<24} {spec.name:<24} "
                  f"{c.kind if c else ''}  paper={c.paper if c else ''}")
    elif args.cmd == "setup-paper2agent":
        from .recruit.paper2agent import install_skill

        for d in install_skill(s.recruit.skill_source, Path("~/.labhq/cache").expanduser()):
            print(f"installed → {d}")
    elif args.cmd == "demo":
        logging.getLogger().setLevel(logging.WARNING)
        asyncio.run(_demo(args.web, args.port))


if __name__ == "__main__":
    main(sys.argv[1:])
