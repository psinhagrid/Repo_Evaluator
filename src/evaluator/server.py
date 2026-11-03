"""Local HTTP API for the Grader Console frontend.

    evaluate serve --workspace workspace --port 8765

Serves what the CLI writes under a workspace folder (cohort.json,
calibration.json, grades/*/report.json), anonymized snapshot lines for the
evidence drawer, and an upload-and-grade job queue. API keys stay in the
server's environment (.env); the browser never sees them.

Binds to 127.0.0.1 by default and has no authentication: it is a local tool
for one instructor. Do not expose it on a network without adding auth.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .env import MissingCredentials, available_providers, check_credentials, resolve_config
from .intake.snapshot import Snapshot
from .scoring.config import ConfigError, load_config

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_FILE_LINES = 2000
DEV_ORIGINS = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"


@dataclass
class Job:
    id: str
    filename: str
    config: str
    models: str | None
    status: str = "queued"  # queued | intake | grading | done | not_graded | failed
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    message: str | None = None
    report_id: str | None = None
    final: float | None = None
    band: str | None = None
    progress: dict = field(default_factory=lambda: {"done": 0, "total": 0})
    run: str | None = None  # submissions/<archive hash> folder


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


class Workspace:
    """Finds results on disk. Ids are short hashes of paths, so nothing outside the workspace is addressable."""

    def __init__(self, root: Path, configs_dir: Path):
        self.root = Path(root).resolve()
        self.configs_dir = Path(configs_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _id(self, p: Path) -> str:
        return hashlib.sha256(str(p.relative_to(self.root)).encode()).hexdigest()[:12]

    def cohorts(self) -> dict[str, Path]:
        return {self._id(p): p for p in sorted(self.root.rglob("cohort.json"))}

    def calibrations(self) -> dict[str, Path]:
        return {self._id(p): p for p in sorted(self.root.rglob("calibration.json"))}

    def reports(self) -> dict[str, Path]:
        """Report id = judge key (one per submission and judge configuration)."""
        out = {}
        for p in sorted(self.root.rglob("grades/*/report.json")):
            out[p.parent.name] = p
        return out

    def run_dir_of(self, report_path: Path) -> Path:
        return report_path.parent.parent.parent  # <run>/grades/<key>/report.json

    def configs(self) -> dict[str, Path]:
        return {p.stem: p for p in sorted(self.configs_dir.glob("*.yaml"))}


def create_app(workspace: Path = Path("workspace"), configs_dir: Path = Path("configs"), llm_factory=None) -> FastAPI:
    ws = Workspace(workspace, configs_dir)
    jobs: dict[str, Job] = {}
    tasks: set[asyncio.Task] = set()

    def make_llm(config):
        if llm_factory is not None:
            return llm_factory(config)
        from .providers import RouterLLM

        return RouterLLM()

    app = FastAPI(title="Grader Console API", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origin_regex=DEV_ORIGINS, allow_methods=["GET", "POST"],
                       allow_headers=["*"])

    # -- discovery --------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "workspace": str(ws.root), "providers": available_providers()}

    @app.get("/api/configs")
    def configs():
        out = []
        for name, path in ws.configs().items():
            try:
                c = load_config(path)
                raw = _read_json_yaml(path)
            except (ConfigError, OSError) as e:
                out.append({"name": name, "error": str(e)})
                continue
            profiles = ["default", *sorted((raw.get("model_profiles") or {}).keys())]
            status = {p: _profile_status(path, p) for p in profiles}
            ready = [p for p in profiles if status[p]["ready"]]
            out.append({"name": name, "assignment": c.assignment, "title": c.title, "policy_version": c.policy_version,
                        "criteria": [{"id": x.id, "name": x.name, "weight": x.weight} for x in c.criteria],
                        "profiles": profiles, "profile_status": status,
                        # What the Grade page should preselect: a profile that can actually run.
                        "suggested_profile": ready[0] if ready else "default"})
        return out

    @app.get("/api/index")
    def index():
        cohorts = []
        for cid, p in ws.cohorts().items():
            v = _read_json(p) or {}
            cohorts.append({"id": cid, "assignment": v.get("assignment"), "policy_version": v.get("policy_version"),
                            "graded": v.get("graded"), "submissions": v.get("submissions"),
                            "path": str(p.parent.relative_to(ws.root))})
        calibrations = []
        for cid, p in ws.calibrations().items():
            v = _read_json(p) or {}
            calibrations.append({"id": cid, "assignment": v.get("assignment"), "passed": v.get("passed"),
                                 "submissions": v.get("submissions"), "path": str(p.parent.relative_to(ws.root))})
        reports = []
        for rid, p in ws.reports().items():
            r = _read_json(p) or {}
            reports.append({"id": rid, "assignment": r.get("assignment"), "title": r.get("title"),
                            "status": r.get("status"), "submission_sha256": r.get("submission_sha256"),
                            "submission_file": r.get("submission_file"), "generated_at": r.get("generated_at"),
                            "final": (r.get("score") or {}).get("final"), "band": (r.get("score") or {}).get("band"),
                            "run": ws.run_dir_of(p).name})
        reports.sort(key=lambda r: r.get("generated_at") or "", reverse=True)
        return {"cohorts": cohorts, "calibrations": calibrations, "reports": reports}

    # -- documents --------------------------------------------------------------------
    @app.get("/api/cohorts/{cid}")
    def cohort(cid: str):
        p = ws.cohorts().get(cid)
        if p is None:
            raise HTTPException(404, "cohort not found")
        return _read_json(p)

    @app.get("/api/cohorts/{cid}/reports")
    def cohort_reports(cid: str):
        p = ws.cohorts().get(cid)
        if p is None:
            raise HTTPException(404, "cohort not found")
        base = p.parent.resolve()
        return [r for rp in sorted(base.rglob("grades/*/report.json")) if (r := _read_json(rp))]

    @app.get("/api/calibrations/{cid}")
    def calibration(cid: str):
        p = ws.calibrations().get(cid)
        if p is None:
            raise HTTPException(404, "calibration not found")
        return _read_json(p)

    @app.get("/api/reports/{rid}")
    def report(rid: str):
        p = _find_report(ws, rid)
        return _read_json(p)

    @app.get("/api/reports/{rid}/file")
    def report_file(rid: str, path: str, start: int = 1, end: int | None = None):
        """Anonymized snapshot lines for the evidence drawer. Only files in the snapshot index are served."""
        p = _find_report(ws, rid)
        snap = Snapshot.load(ws.run_dir_of(p) / "snapshot")
        if path not in snap.files:
            raise HTTPException(404, "file not in snapshot")
        lines = snap.lines(path)
        n = len(lines)
        start = max(1, start)
        end = min(n, end if end is not None else n, start + MAX_FILE_LINES - 1)
        return {"path": path, "total_lines": n, "start": start, "end": end,
                "lines": [{"n": i, "text": lines[i - 1]} for i in range(start, end + 1)] if n else []}

    # -- grading jobs -------------------------------------------------------------------
    @app.get("/api/jobs")
    def list_jobs():
        return sorted((asdict(j) for j in jobs.values()), key=lambda j: j["created_at"], reverse=True)

    @app.get("/api/jobs/{jid}")
    def get_job(jid: str):
        job = jobs.get(jid)
        if job is None:
            raise HTTPException(404, "job not found")
        _update_progress(ws, job)
        return asdict(job)

    @app.post("/api/grade", status_code=202)
    async def grade(file: UploadFile = File(...), config: str = Form(...), models: str | None = Form(None),
                    assignment_text: str | None = Form(None)):
        cfg_path = ws.configs().get(config)
        if cfg_path is None:
            raise HTTPException(400, f"unknown config {config!r}")
        profile = None if models in (None, "", "default") else models
        try:
            cfg = resolve_config(cfg_path, profile, assignment_text)
            check_credentials(cfg)
        except MissingCredentials as e:
            raise HTTPException(400, str(e).replace("pass --models openai", "choose the openai models option")) from e
        except ConfigError as e:
            raise HTTPException(400, str(e)) from e
        if not (file.filename or "").lower().endswith(".zip"):
            raise HTTPException(400, "upload a .zip file")

        jid = uuid.uuid4().hex[:12]
        upload_dir = ws.root / "uploads"
        upload_dir.mkdir(exist_ok=True)
        dest = upload_dir / f"{jid}.zip"
        size = 0
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, "upload exceeds 200 MB")
                out.write(chunk)

        job = Job(id=jid, filename=file.filename or "submission.zip", config=config, models=profile)
        jobs[jid] = job
        task = asyncio.create_task(_run_job(ws, job, dest, cfg, make_llm))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return asdict(job)

    return app


def _profile_status(path: Path, profile: str) -> dict:
    """Which models a profile uses and whether this server has the keys to run them."""
    from .env import available_providers, providers

    try:
        cfg = resolve_config(path, None if profile == "default" else profile)
    except ConfigError as e:
        return {"ready": False, "models": {}, "missing": [], "error": str(e)}
    have = available_providers()
    missing = sorted(p for p in providers(cfg) if not have.get(p))
    return {"ready": not missing, "models": dict(cfg.models), "missing": missing}


def _read_json_yaml(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text()) or {}


def _find_report(ws: Workspace, rid: str) -> Path:
    reports = ws.reports()
    if rid in reports:
        return reports[rid]
    # Allow lookup by submission hash (prefix of at least 8 characters): newest report wins.
    if len(rid) >= 8 and re.fullmatch(r"[0-9a-f]+", rid):
        hits = [(p, _read_json(p) or {}) for p in reports.values()]
        hits = [(p, r) for p, r in hits if (r.get("submission_sha256") or "").startswith(rid)]
        if hits:
            return max(hits, key=lambda h: h[1].get("generated_at") or "")[0]
    raise HTTPException(404, "report not found")


def _update_progress(ws: Workspace, job: Job) -> None:
    if job.status != "grading" or not job.run:
        return
    run = ws.root / "submissions" / job.run
    done = len(list(run.glob("grades/*/criterion_*_r0.json")))
    job.progress = {**job.progress, "done": done}


async def _run_job(ws: Workspace, job: Job, zip_path: Path, config, make_llm) -> None:
    from .grading import GradeOptions, grade_submission
    from .pipeline import run_intake

    from .intake.unzip import sha256_file

    try:
        job.status = "intake"
        # One folder per archive (by content hash): re-submitting the same ZIP reuses its intake
        # and every criterion already graded, so a retry after a failure only pays for what's left.
        sha = await asyncio.to_thread(sha256_file, zip_path)
        run_dir = ws.root / "submissions" / sha[:16]
        job.run = run_dir.name
        meta = _read_json(run_dir / "intake.json") or {}
        if meta.get("status") != "ok":
            if run_dir.exists():
                shutil.rmtree(run_dir)
            intake = await asyncio.to_thread(run_intake, zip_path, config, run_dir)
            if intake.status != "ok":
                job.status, job.message = "not_graded", intake.reason
                return
        job.status = "grading"
        job.progress = {"done": 0, "total": len(config.criteria)}
        report = await grade_submission(run_dir, config, make_llm(config), GradeOptions())
        job.report_id = report.get("judge_key")
        if report.get("status") == "graded":
            job.status = "done"
            job.final = report["score"]["final"]
            job.band = report["score"]["band"]
            job.progress = {"done": len(config.criteria), "total": len(config.criteria)}
        else:
            job.status, job.message = "not_graded", report.get("reason")
    except Exception as e:  # report the failure to the UI instead of losing it in a background task
        job.status = "failed"
        job.message = f"{type(e).__name__}: {e}"[:500]
        (ws.root / "uploads" / f"{job.id}.error.txt").write_text(traceback.format_exc())


def serve(workspace: Path, configs_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(create_app(workspace, configs_dir), host=host, port=port, log_level="info")
