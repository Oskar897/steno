"""The UI's JSON API as plain methods (no HTTP here, see ``steno.ui.server``).

Every handler returns a JSON-able dict or raises ``ApiError``. Nothing here blocks for
long: recording and jobs run on their own threads (``recorder``, ``jobs``).
"""

from __future__ import annotations

import logging
import re
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from steno import __version__, labels, llm, projects, settings, transcript
from steno.config import Config
from steno.profiles import ProfileError, ProfileStore
from steno.secure import SecureStorageError
from steno.ui.enroll import DEFAULT_SECONDS, Enroller, EnrollError
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
STEP_LABELS = {"finalize": "finalize", "clean": "clean-up", "title": "title",
               "summary": "summary", "todo": "to-do"}
QUIET_STEPS = {"title"}  # a failing automatic step that only gets a log line, no error
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

    def run_title(session_dir: Path, params: dict[str, Any], progress: Progress) -> str:
        """Name an untitled meeting from its transcript; a title the user gave stays."""

        def untitled() -> bool:
            return projects.meeting_title(session_dir) == projects.DEFAULT_TITLE

        if not untitled():
            raise RuntimeError("the meeting already has a title")
        progress("waiting for Claude", 0, 0)
        title = llm.suggest_title(session_dir, cfg.llm)
        if not untitled():  # the user named it while Claude was thinking
            raise RuntimeError("the meeting was named meanwhile")
        meeting = projects.rename_meeting(session_dir, title)
        return f"titled: {meeting.title}"

    return {"finalize": run_finalize, "clean": run_clean, "title": run_title,
            "summary": run_summary, "todo": run_todo}


class UiApp:
    def __init__(
        self,
        cfg: Config,
        *,
        engine_factory: EngineFactory | None = None,
        runners: dict[str, Runner] | None = None,
        store_factory: Callable[[], ProfileStore] = ProfileStore,
        enroller_deps: dict[str, Any] | None = None,
        llm_probe: Callable[[], llm.ClaudeStatus] | None = None,
        llm_login: Callable[[Any], Any] | None = None,
        open_url: Callable[[str], Any] | None = None,
    ):
        self.cfg = cfg
        self.bus = EventBus()
        self._store_factory = store_factory
        self.recorder = Recorder(cfg, self.bus, engine_factory)
        # Is Claude Code installed and logged in? Asked with `claude auth status`, kept 60 s.
        self._llm = llm.StatusCache(llm_probe or (lambda: llm.probe_status(cfg.llm)))
        self._llm_login = llm_login or llm.start_login
        self._open_url = open_url or webbrowser.open
        self._login_proc: Any = None
        self._login_lock = threading.Lock()
        base = {kind: self._notice_auth(fn)
                for kind, fn in dict(runners or default_runners(cfg, store_factory)).items()}
        base[AUTO_JOB] = self._auto_runner(base)
        self.jobs = JobManager(self.bus, self._with_sync(base))
        self.recorder.on_stopped = self._after_recording
        self.enroller = Enroller(cfg, self.bus, store_factory=store_factory,
                                 is_recording=lambda: self.recorder.active, **(enroller_deps or {}))

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

    @staticmethod
    def _type(project: projects.Project | None, value: Any) -> str | None:
        """A meeting type name of ``project`` (checked), or None for no type."""
        if value in (None, ""):
            return None
        if project is None or not isinstance(value, str):
            raise ApiError(400, "a meeting type needs a project")
        try:
            t = project.find_type(value)
        except projects.ProjectError as exc:
            raise ApiError(400, str(exc)) from exc
        return t.name if t else None

    def _session_dir(self, session_id: str) -> Path:
        if not _SESSION_ID_RE.match(session_id):
            raise ApiError(404, "no such session")
        d = transcript.resolve_session(session_id)
        if d is None:
            raise ApiError(404, "no such session")
        return d

    def _finalize_needs_audio(self) -> bool:
        """The default finalize ([post] diarization "fast", asr_mode "refined") works from what
        the live run stored; only the thorough / re-transcribing modes read kept audio."""
        post = self.cfg.post
        return post.diarization == "thorough" or post.asr_mode in ("longform", "selective")

    def _notice_auth(self, fn: Runner) -> Runner:
        """A job that fails because Claude is not logged in: forget the cached status, so the
        page's indicator turns to "not logged in" at once."""

        def run(session_dir: Path, params: dict[str, Any], progress: Progress) -> str:
            try:
                return fn(session_dir, params, progress)
            except llm.LlmAuthError:
                self._llm.invalidate()
                self.bus.publish("llm", {})
                raise

        return run

    def _after_recording(self, session_id: str, keep_audio: bool,
                         auto_title: bool = False) -> None:
        s = settings.load()
        can_finalize = keep_audio or not self._finalize_needs_audio()
        steps = ["finalize"] if (s.auto_finalize or self.cfg.post.auto) and can_finalize else []
        if auto_title:  # the dock's per-meeting switch (it starts from the Settings value)
            steps.append("title")
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
            done, failed, skipped = [], [], []
            for step in params.get("steps", []):
                def step_progress(stage: str, d: int = 0, t: int = 0, _step=step) -> None:
                    progress(f"{STEP_LABELS.get(_step, _step)}: {stage}", d, t)

                step_progress("starting")
                try:
                    base[step](session_dir, {}, step_progress)
                    done.append(STEP_LABELS.get(step, step))
                except Exception as exc:
                    if step in QUIET_STEPS:  # e.g. the title: not logged in -> stays untitled
                        log.warning("automatic %s skipped: %s", step, exc)
                        skipped.append(f"{STEP_LABELS.get(step, step)} ({exc})")
                        continue
                    log.exception("automatic %s failed", step)
                    failed.append(f"{STEP_LABELS.get(step, step)} ({exc})")
            if failed and not done:
                raise RuntimeError("failed: " + "; ".join(failed))
            text = ("done: " + ", ".join(done) if done else "nothing to do") + (
                f"; failed: {'; '.join(failed)}" if failed else "")
            return text + (f"; skipped: {'; '.join(skipped)}" if skipped else "")

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
        if self.enroller.active:
            raise ApiError(409, "a voice recording for a person is running; wait for it to finish")
        cfg = self.cfg.audio
        mic = body.get("mic", cfg.capture_mic)
        loopback = body.get("loopback", cfg.capture_loopback)
        keep = body.get("keep_audio", False)
        auto_title = body.get("auto_title", settings.load().auto_title)
        if not all(isinstance(v, bool) for v in (mic, loopback, keep, auto_title)):
            raise ApiError(400, "mic, loopback, keep_audio and auto_title must be true or false")
        pid = body.get("project")
        project = self._project(pid) if pid and pid != "inbox" else None
        title = body.get("title")
        title = clean_name(title, "title") if isinstance(title, str) and title.strip() else None
        mtype = self._type(project, body.get("type"))
        try:
            self.recorder.start(keep_audio=keep, mic=mic, loopback=loopback, project=project,
                                title=title, mtype=mtype, auto_title=auto_title)
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

    def record_auto_title(self, body: dict[str, Any]) -> dict[str, Any]:
        """Switch the automatic title for the meeting being recorded."""
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise ApiError(400, "enabled must be true or false")
        try:
            self.recorder.set_auto_title(enabled)
        except RecorderError as exc:
            raise ApiError(409, str(exc)) from exc
        return {"recorder": self.recorder.snapshot()}

    # -- Claude Code (AI features) -------------------------------------------------

    def llm_status(self, refresh: bool = False) -> dict[str, Any]:
        """Is Claude Code installed and logged in? (``claude auth status``, cached 60 s)"""
        status = self._llm.get(refresh=refresh)
        with self._login_lock:
            waiting = self._login_proc is not None and self._login_proc.poll() is None
        return {"llm": {**status.as_dict(), "login_running": waiting}}

    def llm_login(self) -> dict[str, Any]:
        """Open ``claude auth login`` in a console window; the user finishes it in the browser."""
        with self._login_lock:
            if self._login_proc is not None and self._login_proc.poll() is None:
                return {"started": False, "running": True}
            try:
                proc = self._llm_login(self.cfg.llm)
            except llm.LlmError as exc:
                raise ApiError(409, str(exc)) from exc
            self._login_proc = proc
        threading.Thread(target=self._after_login, args=(proc,), name="claude-login",
                         daemon=True).start()
        return {"started": True, "running": True}

    def _after_login(self, proc: Any) -> None:
        try:
            proc.wait()
        except Exception:
            log.debug("could not wait for the login console", exc_info=True)
            return
        self._llm.invalidate()
        self.bus.publish("llm", {})

    def llm_install_page(self) -> dict[str, Any]:
        """Open the Claude Code install page in the default browser (a fixed address)."""
        try:
            self._open_url(llm.INSTALL_URL)
        except Exception as exc:
            raise ApiError(500, f"could not open the browser: {exc}") from exc
        return {"ok": True}

    # -- settings -----------------------------------------------------------------

    def settings_get(self) -> dict[str, Any]:
        return {"settings": asdict(settings.load())}

    def settings_set(self, body: dict[str, Any]) -> dict[str, Any]:
        if "glossary" in body and not isinstance(body["glossary"], list | str):
            raise ApiError(400, "glossary must be a list of words")
        for key in ("auto_finalize", "auto_title", "auto_clean", "auto_summary", "auto_todo",
                    "transparent", "mini_strip"):
            if key in body and not isinstance(body[key], bool):
                raise ApiError(400, f"{key} must be true or false")
        if "theme" in body and body["theme"] not in settings.THEMES:
            raise ApiError(400, "theme must be system, dark or light")
        scale = body.get("ui_scale", 100)
        if type(scale) is not int or scale not in settings.UI_SCALES:
            allowed = ", ".join(map(str, settings.UI_SCALES))
            raise ApiError(400, f"ui_scale must be one of: {allowed}")
        if "intensity" in body and body["intensity"] not in settings.INTENSITIES:
            raise ApiError(400, "intensity must be subtle, medium or strong")
        if "palette" in body and body["palette"] not in settings.PALETTES:
            raise ApiError(400, "palette must be one of: " + ", ".join(settings.PALETTES))
        s = settings.update(body)
        self.bus.publish("settings", asdict(s))
        return {"settings": asdict(s)}

    # -- projects -----------------------------------------------------------------

    def projects_list(self) -> dict[str, Any]:
        out = []
        for p in projects.list_projects():
            meetings = [m for m in projects.list_meetings(p) if not _archived(m.session)]
            out.append({"id": p.id, "name": p.name, "path": str(p.path),
                        "meetings": len(meetings), "types": _types(p),
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
                            "types": _types(p), "last_at": None}}

    def project_types(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Replace the meeting types: ``{"types": [{"name", "folder"}]}``."""
        self._project(pid)
        types = body.get("types")
        if not isinstance(types, list):
            raise ApiError(400, "types must be a list of {name, folder}")
        try:
            p = projects.set_types(pid, types)
        except projects.ProjectError as exc:
            raise ApiError(400, str(exc)) from exc
        except OSError as exc:
            raise ApiError(409, f"cannot write the project: {exc}") from exc
        self.bus.publish("projects", {})
        return {"types": _types(p)}

    def project_remove(self, pid: str) -> dict[str, Any]:
        self._project(pid)
        projects.remove_project(pid)
        self.bus.publish("projects", {})
        return {"ok": True}

    def project_meetings(self, pid: str) -> dict[str, Any]:
        live = self.recorder.active_session()
        if pid == "inbox":
            items = [(s.dir, projects.meeting_title(s.dir), "", None)
                     for s in projects.inbox_sessions()]
        else:
            project = self._project(pid)
            dirs = {project.type_dir(t): t.name for t in project.types}
            items = []
            for m in projects.list_meetings(project):
                d = transcript.resolve_session(m.session)
                if d is not None:
                    items.append((d, m.title, str(m.folder), dirs.get(m.folder.parent)))
        out = []
        for d, title, folder, mtype in items:
            info = transcript.load_session_info(d)
            out.append({
                "session": info.id, "title": title, "folder": folder, "type": mtype,
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
        if body.get("project") == "inbox":  # back to the inbox: no folder in any project
            if body.get("type") not in (None, ""):
                raise ApiError(400, "the inbox has no meeting types")
            try:
                m = projects.move_to_inbox(d)
            except OSError as exc:
                raise ApiError(409, f"cannot move the meeting folder: {exc}") from exc
            self.bus.publish("projects", {"session": session_id})
            return {"title": m.title, "folder": "", "project": "inbox", "type": None}
        project = self._project(str(body.get("project") or ""))
        mtype = self._type(project, body.get("type"))
        try:
            m = projects.move_meeting(d, project, mtype)
        except OSError as exc:
            raise ApiError(409, f"cannot move the meeting folder: {exc}") from exc
        self.bus.publish("projects", {"session": session_id})
        return {"title": m.title, "folder": str(m.folder), "project": project.id,
                "type": mtype}

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
            "type": projects.type_of(d),
            "started_at": info.started_at,
            "ended_at": info.ended_at,
            "model": info.model,
            "sources": info.sources,
            "recording": info.id == self.recorder.active_session(),
            "has_final": has_final,
            "has_audio": any(d.glob("audio-*.flac")),
            "view": kind,
            "segments": [segment_dict(s) for s in segments],
            # which labels the lines carry: "final" (finalize) or "live"; clean text keeps
            # the labels of the text it was made from
            "speaker_space": "final" if use_final else "live",
            "speakers": self._speakers(d, segments, "final" if use_final else "live"),
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
            # final speakers can be named too (the name goes to the overlapping live cluster
            # and finalize runs again, see ops.name_final_speaker)
            e["can_name"] = is_cluster and (kind == "live" or not named)
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
        if body.get("view") == "final":
            return self._name_final(session_id, d, spec, name, save)
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

    def _name_final(self, session_id: str, d: Path, spec: str, name: str,
                    save: bool) -> dict[str, Any]:
        from steno import ops

        final_id = labels.parse_cluster_spec(spec)
        if final_id is None:
            raise ApiError(400, f"not a speaker cluster: {spec!r}")
        try:
            res = self._store_call(lambda: ops.name_final_speaker(
                d, final_id, name, self._store_factory() if save else None, save_profile=save))
        except ops.OpsError as exc:
            raise ApiError(400, str(exc)) from exc
        note = f"{name}: " + (
            f"voice profile {'created' if res.created else 'extended'} ({res.windows} windows)"
            if res.profile is not None else "named in this meeting")
        try:  # finalize again so the final labels carry the name (seconds)
            self.jobs.submit("finalize", d.name, d, {})
            note += "; updating the transcript"
        except (JobBusy, KeyError):
            note += "; run Finalize to update the transcript"
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
            if self._finalize_needs_audio() and not any(d.glob("audio-*.flac")):
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

    def enroll_start(self, body: dict[str, Any]) -> dict[str, Any]:
        """Record the microphone for a new person, or to add voice to an existing one."""
        name = clean_name(body.get("name"))
        seconds = body.get("seconds", DEFAULT_SECONDS)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ApiError(400, "seconds must be a number")
        found = self._store_call(lambda: self._store_factory().find_by_name(name))
        existing = body.get("existing", found is not None)
        if not isinstance(existing, bool):
            raise ApiError(400, "existing must be true or false")
        if existing and found is None:
            raise ApiError(404, "no such person")
        if not existing and found is not None:
            raise ApiError(409, f"{found.name} already has a voice profile; use Add voice on them")
        if found is not None:
            name = found.name  # keep the stored spelling
        try:
            self.enroller.start(name, float(seconds), existing)
        except EnrollError as exc:
            raise ApiError(409, str(exc)) from exc
        return {"enroll": self.enroller.snapshot()}

    def enroll_status(self) -> dict[str, Any]:
        return {"enroll": self.enroller.snapshot()}

    def enroll_cancel(self) -> dict[str, Any]:
        return {"enroll": self.enroller.cancel()}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def _types(p: projects.Project) -> list[dict[str, str]]:
    return [{"name": t.name, "folder": t.folder} for t in p.types]


def _archived(session_id: str) -> bool:
    d = transcript.resolve_session(session_id)
    return d is not None and projects.is_archived(d)


def _started_at(session_id: str) -> str | None:
    d = transcript.resolve_session(session_id)
    return transcript.load_session_info(d).started_at if d is not None else None
