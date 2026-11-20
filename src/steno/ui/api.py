"""The UI's JSON API as plain methods (no HTTP here, see ``steno.ui.server``).

Every handler returns a JSON-able dict or raises ``ApiError``. Nothing here blocks for
long: recording and jobs run on their own threads (``recorder``, ``jobs``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from steno import __version__, labels, projects, settings, transcript
from steno.config import Config
from steno.profiles import ProfileError, ProfileStore
from steno.secure import SecureStorageError
from steno.ui.events import EventBus
from steno.ui.jobs import JobBusy, JobManager, Progress, Runner
from steno.ui.recorder import EngineFactory, Recorder, RecorderError, segment_dict

log = logging.getLogger(__name__)

MAX_NAME_LEN = 80
MAX_SPEAKERS = 20
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PROFILE_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_PROJECT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_PATH_LEN = 1000
DOCS = {"summary": "summary.md", "todo": "todo.md"}
AUTO_JOB = "auto"  # the jobs that run by themselves after a recording (Settings)
STEP_LABELS = {"finalize": "finalize", "clean": "clean-up", "summary": "summary",
               "todo": "to-do"}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def clean_name(value: Any, what: str = "name") -> str:
    if not isinstance(value, str):
        raise ApiError(400, f"{what} must be text")
    text = _CONTROL_RE.sub(" ", value).strip()
    if not text:
        raise ApiError(400, f"{what} must not be empty")
    if len(text) > MAX_NAME_LEN:
        raise ApiError(400, f"{what} is longer than {MAX_NAME_LEN} characters")
    return text


def default_runners(cfg: Config, store_factory: Callable[[], ProfileStore]) -> dict[str, Runner]:
    """Finalize / clean-up / summary / to-do exactly as the CLI runs them."""

    def run_finalize(session_dir: Path, params: dict[str, Any], progress: Progress) -> str:
        from steno import finalize

        res = finalize.finalize_session(
            session_dir, cfg, store_factory(), num_speakers=params.get("speakers"),
            progress=progress)
        text = f"{res.segments} lines, {len(res.speakers)} speaker(s)"
        if len(res.speakers) == 1 and params.get("speakers") is None:
            text += " (only one found; set the number of speakers if you know it)"
        return text

    def run_clean(session_dir: Path, params: dict[str, Any], progress: Progress) -> str:
        from steno import clean

        res = clean.clean_session(session_dir, cfg.llm, progress=progress)
        return f"{clean.summary_line(res.meta)} ({res.meta['source']} text)"

    def run_summary(session_dir: Path, params: dict[str, Any], progress: Progress) -> str:
        from steno import llm

        progress("waiting for Claude", 0, 0)
        _, kind = llm.summarize(session_dir, cfg.llm)
        return f"summary saved ({kind} transcript)"

    def run_todo(session_dir: Path, params: dict[str, Any], progress: Progress) -> str:
        from steno import llm

        progress("waiting for Claude", 0, 0)
        items, dropped, kind = llm.extract_todos(
            session_dir, cfg.llm, person=params.get("person"))
        return f"{len(items)} to-do item(s) ({kind} transcript), {dropped} dropped without evidence"

    return {"finalize": run_finalize, "clean": run_clean, "summary": run_summary,
            "todo": run_todo}


class UiApp:
    def __init__(
        self,
        cfg: Config,
        *,
        engine_factory: EngineFactory | None = None,
        runners: dict[str, Runner] | None = None,
        store_factory: Callable[[], ProfileStore] = ProfileStore,
    ):
        self.cfg = cfg
        self.bus = EventBus()
        self._store_factory = store_factory
        self.recorder = Recorder(cfg, self.bus, engine_factory)
        base = dict(runners or default_runners(cfg, store_factory))
        base[AUTO_JOB] = self._auto_runner(base)
        self.jobs = JobManager(self.bus, self._with_sync(base))
        self.recorder.on_stopped = self._after_recording

    # -- helpers ------------------------------------------------------------------

    def _with_sync(self, runners: dict[str, Runner]) -> dict[str, Runner]:
        """After every job, copy its results into the meeting folder."""

        def wrap(fn: Runner) -> Runner:
            def run(session_dir: Path, params: dict[str, Any], progress: Progress) -> str:
                result = fn(session_dir, params, progress)
                try:
                    projects.sync(session_dir,
                                  recording=session_dir.name == self.recorder.active_session())
                    project = projects.project_of(session_dir)
                    if project is not None:
                        projects.write_index(project, self.recorder.active_session())
                except Exception:
                    log.exception("Could not update the meeting folder")
                self.bus.publish("projects", {"session": session_dir.name})
                return result
            return run

        return {kind: wrap(fn) for kind, fn in runners.items()}

    def _project(self, pid: str) -> projects.Project:
        if not _PROJECT_ID_RE.match(pid or ""):
            raise ApiError(404, "no such project")
        try:
            return projects.get_project(pid)
        except projects.ProjectError as exc:
            raise ApiError(404, str(exc)) from exc

    def _session_dir(self, session_id: str) -> Path:
        if not _SESSION_ID_RE.match(session_id):
            raise ApiError(404, "no such session")
        d = transcript.resolve_session(session_id)
        if d is None:
            raise ApiError(404, "no such session")
        return d

    def _after_recording(self, session_id: str, keep_audio: bool) -> None:
        s = settings.load()
        steps = ["finalize"] if self.cfg.post.auto and keep_audio else []
        steps += [kind for kind, on in (("clean", s.auto_clean), ("summary", s.auto_summary),
                                        ("todo", s.auto_todo)) if on]
        steps = [k for k in steps if k in self.jobs.kinds]
        if not steps:
            return
        try:  # finalize as with `[post] auto = true`, then the switches from Settings
            self.jobs.submit(AUTO_JOB, session_id, self._session_dir(session_id),
                             {"steps": steps})
        except (JobBusy, ApiError) as exc:
            log.warning("automatic jobs after the recording skipped: %s", exc)

    @staticmethod
    def _auto_runner(base: dict[str, Runner]) -> Runner:
        """Run ``params["steps"]`` one after the other; a failing step does not stop the rest."""

        def run(session_dir: Path, params: dict[str, Any], progress: Progress) -> str:
            done, failed = [], []
            for step in params.get("steps", []):
                def step_progress(stage: str, d: int = 0, t: int = 0, _step=step) -> None:
                    progress(f"{STEP_LABELS.get(_step, _step)}: {stage}", d, t)

                step_progress("starting")
                try:
                    base[step](session_dir, {}, step_progress)
                    done.append(STEP_LABELS.get(step, step))
                except Exception as exc:
                    log.exception("automatic %s failed", step)
                    failed.append(f"{STEP_LABELS.get(step, step)} ({exc})")
            if failed and not done:
                raise RuntimeError("failed: " + "; ".join(failed))
            return "done: " + ", ".join(done) + (
                f"; failed: {'; '.join(failed)}" if failed else "")

        return run

    @staticmethod
    def _store_call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except ProfileError as exc:
            raise ApiError(400, str(exc)) from exc
        except SecureStorageError as exc:
            raise ApiError(500, f"voice profile storage: {exc}") from exc

    # -- status -------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        job = self.jobs.running()
        return {
            "version": __version__,
            "recorder": self.recorder.snapshot(),
            "job": job.as_dict() if job else None,
            "jobs": self.jobs.list()[:5],
            "config": {
                "capture_mic": self.cfg.audio.capture_mic,
                "capture_loopback": self.cfg.audio.capture_loopback,
                "asr_model": self.cfg.asr.model,
                "llm_model": self.cfg.llm.model,
                "llm_language": self.cfg.llm.language,
                "post_auto": self.cfg.post.auto,
                "speakers_enabled": self.cfg.speakers.enabled,
            },
        }

    # -- recording ----------------------------------------------------------------

    def record_start(self, body: dict[str, Any]) -> dict[str, Any]:
        cfg = self.cfg.audio
        mic = body.get("mic", cfg.capture_mic)
        loopback = body.get("loopback", cfg.capture_loopback)
        keep = body.get("keep_audio", False)
        if not all(isinstance(v, bool) for v in (mic, loopback, keep)):
            raise ApiError(400, "mic, loopback and keep_audio must be true or false")
        pid = body.get("project")
        project = self._project(pid) if pid and pid != "inbox" else None
        title = body.get("title")
        title = clean_name(title, "title") if isinstance(title, str) and title.strip() else None
        try:
            self.recorder.start(keep_audio=keep, mic=mic, loopback=loopback, project=project,
                                title=title)
        except RecorderError as exc:
            raise ApiError(409, str(exc)) from exc
        return {"recorder": self.recorder.snapshot()}

    def record_pause(self) -> dict[str, Any]:
        try:
            self.recorder.pause()
        except RecorderError as exc:
            raise ApiError(409, str(exc)) from exc
        return {"recorder": self.recorder.snapshot()}

    def record_resume(self) -> dict[str, Any]:
        try:
            self.recorder.resume()
        except RecorderError as exc:
            raise ApiError(409, str(exc)) from exc
        return {"recorder": self.recorder.snapshot()}

    # -- settings -----------------------------------------------------------------

    def settings_get(self) -> dict[str, Any]:
        return {"settings": asdict(settings.load())}

    def settings_set(self, body: dict[str, Any]) -> dict[str, Any]:
        if "glossary" in body and not isinstance(body["glossary"], list | str):
            raise ApiError(400, "glossary must be a list of words")
        for key in ("auto_clean", "auto_summary", "auto_todo", "transparent"):
            if key in body and not isinstance(body[key], bool):
                raise ApiError(400, f"{key} must be true or false")
        if "theme" in body and body["theme"] not in settings.THEMES:
            raise ApiError(400, "theme must be system, dark or light")
        s = settings.update(body)
        self.bus.publish("settings", asdict(s))
        return {"settings": asdict(s)}

    # -- projects -----------------------------------------------------------------

    def projects_list(self) -> dict[str, Any]:
        out = []
        for p in projects.list_projects():
            meetings = [m for m in projects.list_meetings(p) if not _archived(m.session)]
            out.append({"id": p.id, "name": p.name, "path": str(p.path),
                        "meetings": len(meetings),
                        "last_at": _started_at(meetings[-1].session) if meetings else None})
        inbox = [s for s in projects.inbox_sessions() if not projects.is_archived(s.dir)]
        return {"projects": out, "inbox": len(inbox)}

    def project_add(self, body: dict[str, Any]) -> dict[str, Any]:
        path = body.get("path")
        if not isinstance(path, str) or not path.strip() or len(path) > MAX_PATH_LEN:
            raise ApiError(400, "path must be a folder path")
        name = body.get("name")
        if name is not None:
            name = clean_name(name, "name")
        try:
            p = projects.add_project(path.strip(), name=name, create=bool(body.get("create")))
        except projects.ProjectError as exc:
            raise ApiError(400, str(exc)) from exc
        except OSError as exc:
            raise ApiError(400, f"cannot use that folder: {exc}") from exc
        self.bus.publish("projects", {})
        return {"project": {"id": p.id, "name": p.name, "path": str(p.path), "meetings": 0,
                            "last_at": None}}

    def project_remove(self, pid: str) -> dict[str, Any]:
        self._project(pid)
        projects.remove_project(pid)
        self.bus.publish("projects", {})
        return {"ok": True}

    def project_meetings(self, pid: str) -> dict[str, Any]:
        live = self.recorder.active_session()
        if pid == "inbox":
            items = [(s.dir, projects.meeting_title(s.dir), "") for s in projects.inbox_sessions()]
        else:
            items = []
            for m in projects.list_meetings(self._project(pid)):
                d = transcript.resolve_session(m.session)
                if d is not None:
                    items.append((d, m.title, str(m.folder)))
        out = []
        for d, title, folder in items:
            info = transcript.load_session_info(d)
            out.append({
                "session": info.id, "title": title, "folder": folder,
                "started_at": info.started_at, "ended_at": info.ended_at,
                "recording": info.id == live, "segments": info.segments,
                "has_summary": (d / "summary.md").is_file(),
                "has_todo": (d / "todo.md").is_file(),
                "has_clean": (d / "clean.jsonl").is_file(),
                "archived": projects.is_archived(d),
            })
        return {"meetings": out}

    def meeting_rename(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        d = self._session_dir(session_id)
        title = clean_name(body.get("title"), "title")
        try:
            m = projects.rename_meeting(d, title)
        except OSError as exc:
            raise ApiError(409, f"cannot rename the meeting folder: {exc}") from exc
        self.bus.publish("projects", {"session": session_id})
        return {"title": m.title, "folder": str(m.folder)}

    def meeting_move(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        d = self._session_dir(session_id)
        if session_id == self.recorder.active_session():
            raise ApiError(409, "stop the recording before moving it")
        if body.get("project") == "inbox":
            raise ApiError(400, "meetings can be moved to a project, not back to the inbox")
        project = self._project(str(body.get("project") or ""))
        try:
            m = projects.move_meeting(d, project)
        except OSError as exc:
            raise ApiError(409, f"cannot move the meeting folder: {exc}") from exc
        self.bus.publish("projects", {"session": session_id})
        return {"title": m.title, "folder": str(m.folder), "project": project.id}

    def meeting_archive(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        d = self._session_dir(session_id)
        archived = body.get("archived", True)
        if not isinstance(archived, bool):
            raise ApiError(400, "archived must be true or false")
        if archived and session_id == self.recorder.active_session():
            raise ApiError(409, "stop the recording before archiving it")
        projects.set_archived(d, archived)
        self.bus.publish("projects", {"session": session_id})
        return {"archived": archived}

    def meeting_delete(self, session_id: str) -> dict[str, Any]:
        d = self._session_dir(session_id)
        if session_id == self.recorder.active_session():
            raise ApiError(409, "stop the recording before deleting it")
        job = self.jobs.running()
        if job is not None and job.session == session_id:
            raise ApiError(409, "wait for the running job on this meeting to finish")
        try:
            projects.delete_meeting(d)
        except OSError as exc:
            raise ApiError(409, f"cannot delete the meeting: {exc}") from exc
        self.bus.publish("projects", {"session": session_id})
        self.bus.publish("sessions", {"session": session_id})
        return {"ok": True}

    def session_doc(self, session_id: str, kind: str) -> dict[str, Any]:
        d = self._session_dir(session_id)
        if kind not in DOCS:
            raise ApiError(404, "no such document")
        text = _read_text(d / DOCS[kind])
        if text is None:
            raise ApiError(404, f"this meeting has no {kind} yet")
        return {"markdown": text}

    def record_stop(self) -> dict[str, Any]:
        try:
            self.recorder.stop()
        except RecorderError as exc:
            raise ApiError(409, str(exc)) from exc
        return {"recorder": self.recorder.snapshot()}

    # -- sessions -----------------------------------------------------------------

    def sessions(self) -> dict[str, Any]:
        live = self.recorder.active_session()
        out = []
        for info in reversed(transcript.list_sessions()):
            d = info.dir
            out.append({
                "id": info.id,
                "started_at": info.started_at,
                "ended_at": info.ended_at,
                "segments": info.segments,
                "sources": info.sources,
                "recording": info.id == live,
                "has_final": (d / "final.jsonl").is_file(),
                "has_clean": (d / "clean.jsonl").is_file(),
                "has_summary": (d / "summary.md").is_file(),
                "has_todo": (d / "todo.md").is_file(),
                "has_audio": any(d.glob("audio-*.flac")),
            })
        return {"sessions": out}

    def session(self, session_id: str, view: str | None = None) -> dict[str, Any]:
        from steno import clean, finalize, llm

        d = self._session_dir(session_id)
        info = transcript.load_session_info(d)
        has_final = finalize.has_final(d)
        if view not in (None, "live", "final"):
            raise ApiError(400, "view must be live or final")
        use_final = has_final and view != "live"
        if view == "final" and not has_final:
            raise ApiError(404, "this session has no final transcript")
        if use_final:
            segments = finalize.read_final(d)
            kind = "final"
        else:
            segments = labels.resolve_segments(d, transcript.read_best(d))
            kind = "live"
        if view is None:  # the cleaned text while it still matches what it was made from
            cleaned = clean.clean_overlay(d, segments) if segments else None
            if cleaned is not None:
                segments, kind = cleaned, "clean"
        clean_info = clean.status(d)
        project = projects.project_of(d)
        return {
            "id": info.id,
            "title": projects.meeting_title(d),
            "project": project.id if project is not None else None,
            "started_at": info.started_at,
            "ended_at": info.ended_at,
            "model": info.model,
            "sources": info.sources,
            "recording": info.id == self.recorder.active_session(),
            "has_final": has_final,
            "has_audio": any(d.glob("audio-*.flac")),
            "view": kind,
            "segments": [segment_dict(s) for s in segments],
            "speakers": self._speakers(d, segments, kind),
            "summary": _read_text(d / llm.SUMMARY_NAME),
            "todo": _read_text(d / llm.TODO_MD_NAME),
            # which text the clean-up sends, and which one summary / to-do would send (text only)
            "clean_source": "final" if has_final else "live",
            "clean": clean_info,
            "llm_source": "clean" if clean_info and clean_info["fresh"] else (
                "final" if has_final else "live"),
        }

    @staticmethod
    def _speakers(d: Path, segments: list, kind: str) -> list[dict[str, Any]]:
        data = labels.SessionLabels(d).load()
        agg: dict[str, dict[str, Any]] = {}
        for s in segments:
            key = s.speaker_id or f"name:{s.speaker}"
            e = agg.get(key)
            if e is None:
                e = agg[key] = {
                    "id": s.speaker_id, "name": s.speaker, "segments": 0, "seconds": 0.0,
                    "provisional": False,
                }
            e["segments"] += 1
            e["seconds"] = round(e["seconds"] + max(0.0, s.t_end - s.t_start), 1)
            e["provisional"] = bool(s.provisional)  # state of the latest line
        out = []
        for e in agg.values():
            sid = e["id"]
            is_cluster = sid is not None and labels.cluster_number(sid) is not None
            named = bool(data["clusters"].get(sid, {}).get("name")) if is_cluster else True
            if kind == "final":  # final labels are a new namespace: naming happens on live lines
                named = not (is_cluster and e["name"] == f"Speaker {labels.cluster_number(sid)}")
            e["can_name"] = kind == "live" and is_cluster
            e["named"] = named
            out.append(e)
        out.sort(key=lambda e: -e["seconds"])
        return out

    def name_speaker(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from steno import ops

        d = self._session_dir(session_id)
        name = clean_name(body.get("name"))
        spec = body.get("speaker")
        save = body.get("save_profile", False)
        if not isinstance(spec, str) or not isinstance(save, bool):
            raise ApiError(400, "speaker must be text and save_profile true or false")
        cluster = labels.parse_cluster_spec(spec)
        if cluster is None:
            raise ApiError(400, f"not a speaker cluster: {spec!r}")
        try:
            if save:  # biometric: learns the voice into an encrypted profile
                res = self._store_call(
                    lambda: ops.name_cluster(d, cluster, name, self._store_factory()))
                note = (f"{name}: voice profile {'created' if res.created else 'extended'} "
                        f"from {res.used} segment(s)")
            else:  # label only, this session, nothing biometric stored
                session_labels = labels.SessionLabels(d)
                root = session_labels.resolve_root(cluster)
                ids = labels.effective_speaker_ids(d, transcript.read_segments(d))
                if root not in ids.values():
                    raise ApiError(400, f"session has no speaker {root}")
                session_labels.set_cluster_name(root, name, None)
                labels.regenerate_markdown(d)
                note = f"{name}: named in this session only"
        except ops.OpsError as exc:
            raise ApiError(400, str(exc)) from exc
        self.bus.publish("names", {"session": session_id})
        return {"ok": True, "message": note}

    # -- jobs ---------------------------------------------------------------------

    def start_job(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        d = self._session_dir(session_id)
        kind = body.get("kind")
        if kind not in self.jobs.kinds or kind == AUTO_JOB:
            raise ApiError(400, f"unknown job kind (use one of: {', '.join(self.jobs.kinds)})")
        params: dict[str, Any] = {}
        if kind == "finalize":
            speakers = body.get("speakers")
            if speakers is not None:
                if (not isinstance(speakers, int) or isinstance(speakers, bool)
                        or not 1 <= speakers <= MAX_SPEAKERS):
                    raise ApiError(400, f"speakers must be a whole number 1-{MAX_SPEAKERS}")
                params["speakers"] = speakers
            if session_id == self.recorder.active_session():
                raise ApiError(409, "stop the recording before finalizing it")
            if not any(d.glob("audio-*.flac")):
                raise ApiError(400, "no kept audio in this session; record with Keep audio")
        elif kind == "todo" and body.get("person") not in (None, ""):
            params["person"] = clean_name(body.get("person"), "person")
        try:
            job = self.jobs.submit(kind, d.name, d, params)
        except JobBusy as exc:
            raise ApiError(409, str(exc)) from exc
        return {"job": job.as_dict()}

    def job_list(self) -> dict[str, Any]:
        return {"jobs": self.jobs.list()}

    # -- people -------------------------------------------------------------------

    def people(self) -> dict[str, Any]:
        profiles = self._store_call(lambda: self._store_factory().list())
        return {"people": [
            {"id": p.id, "name": p.name, "embeddings": len(p.embeddings), "created": p.created,
             "updated": p.updated}
            for p in profiles
        ]}

    def _profile(self, store: ProfileStore, profile_id: str):
        if not _PROFILE_ID_RE.match(profile_id):
            raise ApiError(404, "no such person")
        profile = self._store_call(lambda: store.get(profile_id))
        if profile is None:
            raise ApiError(404, "no such person")
        return profile

    def person_rename(self, profile_id: str, body: dict[str, Any]) -> dict[str, Any]:
        new = clean_name(body.get("name"))
        store = self._store_factory()
        profile = self._profile(store, profile_id)
        renamed = self._store_call(lambda: store.rename(profile.name, new))
        count = 0
        for info in transcript.list_sessions():  # same as `steno people rename`
            if labels.SessionLabels(info.dir).rename_profile(renamed.id, renamed.name):
                labels.regenerate_markdown(info.dir)
                count += 1
        self.bus.publish("names", {"session": None})
        return {"ok": True, "message": f"renamed to {renamed.name} ({count} session(s) updated)"}

    def person_delete(self, profile_id: str) -> dict[str, Any]:
        store = self._store_factory()
        profile = self._profile(store, profile_id)
        self._store_call(lambda: store.delete(profile.name))
        return {"ok": True, "message": f"deleted {profile.name} and their voice data"}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def _archived(session_id: str) -> bool:
    d = transcript.resolve_session(session_id)
    return d is not None and projects.is_archived(d)


def _started_at(session_id: str) -> str | None:
    d = transcript.resolve_session(session_id)
    return transcript.load_session_info(d).started_at if d is not None else None
