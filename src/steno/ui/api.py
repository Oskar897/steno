"""The UI's JSON API as plain methods (no HTTP here, see ``steno.ui.server``).

Every handler returns a JSON-able dict or raises ``ApiError``. Nothing here blocks for
long: recording and jobs run on their own threads (``recorder``, ``jobs``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from steno import __version__, labels, transcript
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
    """Finalize / summary / to-do exactly as the CLI runs them."""

    def run_finalize(session_dir: Path, params: dict[str, Any], progress: Progress) -> str:
        from steno import finalize

        res = finalize.finalize_session(
            session_dir, cfg, store_factory(), num_speakers=params.get("speakers"),
            progress=progress)
        text = f"{res.segments} lines, {len(res.speakers)} speaker(s)"
        if len(res.speakers) == 1 and params.get("speakers") is None:
            text += " (only one found; set the number of speakers if you know it)"
        return text

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

    return {"finalize": run_finalize, "summary": run_summary, "todo": run_todo}


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
        self.jobs = JobManager(self.bus, runners or default_runners(cfg, store_factory))
        self.recorder.on_stopped = self._after_recording

    # -- helpers ------------------------------------------------------------------

    def _session_dir(self, session_id: str) -> Path:
        if not _SESSION_ID_RE.match(session_id):
            raise ApiError(404, "no such session")
        d = transcript.resolve_session(session_id)
        if d is None:
            raise ApiError(404, "no such session")
        return d

    def _after_recording(self, session_id: str, keep_audio: bool) -> None:
        if not (self.cfg.post.auto and keep_audio):
            return
        try:  # same as `[post] auto = true` in `steno record`
            self.jobs.submit("finalize", session_id, self._session_dir(session_id), {})
        except (JobBusy, ApiError) as exc:
            log.warning("auto-finalize skipped: %s", exc)

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
        try:
            self.recorder.start(keep_audio=keep, mic=mic, loopback=loopback)
        except RecorderError as exc:
            raise ApiError(409, str(exc)) from exc
        return {"recorder": self.recorder.snapshot()}

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
                "has_summary": (d / "summary.md").is_file(),
                "has_todo": (d / "todo.md").is_file(),
                "has_audio": any(d.glob("audio-*.flac")),
            })
        return {"sessions": out}

    def session(self, session_id: str, view: str | None = None) -> dict[str, Any]:
        from steno import finalize, llm

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
            segments = labels.resolve_segments(d, transcript.read_segments(d))
            kind = "live"
        return {
            "id": info.id,
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
            # which transcript the summary / to-do buttons would send (text only)
            "llm_source": "final" if has_final else "live",
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
        if kind not in self.jobs.kinds:
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
