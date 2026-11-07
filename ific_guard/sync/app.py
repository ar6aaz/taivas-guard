"""Sync service: keeps IFIC databases up to date, from ITU on a cron schedule or by upload."""
import asyncio
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import Artifact, SyncRun
from ..schemas import ArtifactOut, SyncRunOut, UploadOut
from ..service import create_app, require_api_key
from .fetch import MDB_SUFFIXES, BadArtifact
from .runner import _running, freshness, has_run_before, ingest_file, run_sync, scheduled_pass, seconds_until_next_run

router = APIRouter()


@router.get("/issues", response_model=list[ArtifactOut])
def list_issues(domain: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100):
    query = select(Artifact).order_by(Artifact.issue_no.desc(), Artifact.id.desc()).limit(limit)
    if domain:
        query = query.where(Artifact.domain == domain)
    with session_scope() as s:
        return list(s.scalars(query))


@router.post(
    "/issues/upload",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
def upload_issue(
    response: Response,
    file: Annotated[UploadFile, File(description="An IFIC .mdb or .accdb, or a .zip containing one")],
    domain: Annotated[Literal["space", "terrestrial"] | None, Form()] = None,
    issue_no: Annotated[int | None, Form(ge=1)] = None,
):
    """Adds an issue from a file. Domain and issue come from ITU-style names (ific3074, brific3076) if not given.

    The file then goes through the same checks, screening and notification as a download.
    """
    settings = get_settings()
    name = PurePosixPath((file.filename or "").replace("\\", "/")).name  # never trust a client's path
    name = re.sub(r"[^\w.\- ]", "_", name)
    suffix = Path(name).suffix.lower()
    if suffix != ".zip" and suffix not in MDB_SUFFIXES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a .zip, .mdb or .accdb file.")
    limit = settings.max_zip_bytes if suffix == ".zip" else settings.max_mdb_bytes
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.data_dir, prefix=".up-") as tmp:
        target = Path(tmp) / name
        with target.open("wb") as out:
            received = 0
            while chunk := file.file.read(1024 * 1024):
                received += len(chunk)
                if received > limit:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File is over the {limit} byte limit.")
                out.write(chunk)
        try:
            artifact, is_new = ingest_file(target, domain, issue_no, f"upload:{name}")
        except BadArtifact as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if not is_new:
        response.status_code = status.HTTP_200_OK
    return UploadOut(artifact=ArtifactOut.model_validate(artifact), new=is_new)


@router.get("/runs", response_model=list[SyncRunOut])
def list_runs(limit: Annotated[int, Query(ge=1, le=200)] = 20):
    with session_scope() as s:
        return list(s.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)))


@router.get("/status")
def sync_status() -> dict:
    return {
        "running": _running.locked(),
        "schedule": get_settings().sync_cron,
        "next_run_in_s": round(seconds_until_next_run()),
        "domains": freshness(),
    }


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_api_key)])
def trigger_sync(background: BackgroundTasks) -> dict[str, str]:
    if _running.locked():
        raise HTTPException(status.HTTP_409_CONFLICT, "A sync is already running.")
    background.add_task(run_sync, "manual")
    return {"status": "started"}


async def _scheduler() -> None:
    """Runs on the cron schedule. A brand new install syncs at once rather than waiting for the first slot."""
    if await asyncio.to_thread(has_run_before):
        await asyncio.sleep(await asyncio.to_thread(seconds_until_next_run))
    while True:
        await asyncio.sleep(await asyncio.to_thread(scheduled_pass))


app = create_app("ific-sync", router, [_scheduler])
