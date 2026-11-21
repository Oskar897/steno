"""Claude Code status, readable CLI errors and the automatic meeting title.

Everything uses fake runners, fake processes and tmp dirs: the real CLI never runs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from steno import llm, projects, settings
from steno.config import Config, LlmConfig
from steno.transcript import Segment, SessionWriter
from steno.ui.api import default_runners

from .test_llm import Recorder, make_session
from .test_ui import LINES, burst_audio, fake_engine_factory, make_ui, wait_for
from .test_ui import make_session as make_ui_session

__all__ = ["make_ui"]  # fixture re-export

CFG = LlmConfig()
OAUTH = "Failed to authenticate: OAuth session expired and could not be refreshed"


def cli_json(result, *, is_error=True, **extra):
    return json.dumps({"type": "result", "subtype": "error", "is_error": is_error,
                       "result": result, "terminal_reason": "api_error", **extra})


# --------------------------------------------------------------------------- readable errors


def test_expired_login_on_a_failing_exit_is_a_friendly_auth_error():
    """The real reason sits in the JSON `result` on stdout, not in stderr."""
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, cli_json(OAUTH) + "\n", "")

    with pytest.raises(llm.LlmAuthError) as exc:
        llm.run_claude("s", "t", CFG, run)
    assert str(exc.value) == llm.AUTH_MESSAGE
    assert "{" not in str(exc.value)


def test_error_flag_with_exit_zero_is_classified_too():
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, cli_json("Invalid API key"), "")

    with pytest.raises(llm.LlmAuthError):
        llm.run_claude("s", "t", CFG, run)


@pytest.mark.parametrize("text", [
    "Your credit balance is too low to access the API",
    "Claude AI usage limit reached|1760000000",
])
def test_usage_limit_and_credit_balance_are_friendly(text):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, cli_json(text), "")

    with pytest.raises(llm.LlmLimitError) as exc:
        llm.run_claude("s", "t", CFG, run)
    assert str(exc.value) == llm.LIMIT_MESSAGE


def test_other_json_failures_show_the_result_text_not_the_json():
    def run(cmd, **kw):
        banner = "starting up\n" + cli_json("The model is busy, try later")
        return subprocess.CompletedProcess(cmd, 1, banner, "")

    with pytest.raises(llm.LlmError) as exc:
        llm.run_claude("s", "t", CFG, run)
    text = str(exc.value)
    assert not isinstance(exc.value, llm.LlmAuthError)
    assert "The model is busy, try later" in text and "{" not in text and "exit 1" in text


def test_long_failure_text_is_cut():
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "x" * 1000)

    with pytest.raises(llm.LlmError) as exc:
        llm.run_claude("s", "t", CFG, run)
    assert len(str(exc.value)) < 400


# --------------------------------------------------------------------------- finding the CLI


def test_find_cli_looks_in_the_usual_install_places(tmp_path, monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert llm.find_cli("claude") is None
    npm = tmp_path / "appdata" / "npm"
    npm.mkdir(parents=True)
    (npm / "claude.cmd").write_text("", encoding="utf-8")
    assert llm.find_cli("claude") == str(npm / "claude.cmd")
    local = tmp_path / "home" / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "claude.exe").write_text("", encoding="utf-8")
    assert llm.find_cli("claude") == str(local / "claude.exe")  # earlier in the list
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/on/path/claude")
    assert llm.find_cli("claude") == "/on/path/claude"  # PATH wins


# --------------------------------------------------------------------------- status


def status_runner(stdout, returncode=0, stderr="", seen=None):
    def run(cmd, **kw):
        if seen is not None:
            seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return run


@pytest.fixture
def cli_present(monkeypatch):
    monkeypatch.setattr(llm, "find_cli", lambda command: "/bin/claude")


def test_status_ok_keeps_method_and_plan_but_never_the_account(cli_present):
    out = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max",
                      "email": "someone@example.invalid", "orgName": "Some Org"})
    seen: list = []
    st = llm.probe_status(CFG, status_runner(out, seen=seen))
    assert st == llm.ClaudeStatus("ok", method="claude.ai", plan="max")
    assert "example.invalid" not in json.dumps(st.as_dict())
    assert seen[0][0] == ["/bin/claude", "auth", "status"]
    assert seen[0][1]["timeout"] <= 30  # short


def test_status_logged_out_reads_json_even_on_exit_one(cli_present):
    out = json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"})
    assert llm.probe_status(CFG, status_runner(out, returncode=1)).state == "logged_out"


def test_status_missing_when_the_cli_is_not_found(monkeypatch):
    monkeypatch.setattr(llm, "find_cli", lambda command: None)

    def never(cmd, **kw):
        raise AssertionError("must not run")

    st = llm.probe_status(CFG, never)
    assert st.state == "missing" and "claude" in st.detail


def test_status_error_cases(cli_present):
    assert llm.probe_status(CFG, status_runner("garbage", returncode=2)).state == "error"
    assert llm.probe_status(CFG, status_runner("", 1, "Not logged in")).state == "logged_out"

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 20)

    assert llm.probe_status(CFG, slow).state == "error"

    def gone(cmd, **kw):
        raise FileNotFoundError()

    assert llm.probe_status(CFG, gone).state == "missing"


def test_status_cache_ttl_refresh_and_invalidate():
    now = [100.0]
    calls = []

    def probe():
        calls.append(1)
        return llm.ClaudeStatus("ok")

    cache = llm.StatusCache(probe, ttl=60, clock=lambda: now[0])
    cache.get()
    cache.get()
    assert len(calls) == 1
    cache.get(refresh=True)
    assert len(calls) == 2
    now[0] += 59
    cache.get()
    assert len(calls) == 2
    now[0] += 2
    cache.get()
    assert len(calls) == 3
    cache.invalidate()
    cache.get()
    assert len(calls) == 4


def test_status_cache_turns_a_crashing_probe_into_an_error_state():
    def probe():
        raise RuntimeError("boom")

    st = llm.StatusCache(probe).get()
    assert st.state == "error" and "boom" in st.detail


# --------------------------------------------------------------------------- login


def test_login_opens_a_visible_console_on_windows(cli_present, monkeypatch):
    seen = []
    proc = object()

    def popen(cmd, **kw):
        seen.append((cmd, kw))
        return proc

    assert llm.start_login(CFG, popen) is proc
    cmd, kw = seen[0]
    assert cmd == ["/bin/claude", "auth", "login"]
    if sys.platform == "win32":
        assert kw["creationflags"] == subprocess.CREATE_NEW_CONSOLE
        assert "startupinfo" not in kw  # not the hidden-window options of the normal calls
    monkeypatch.setattr(llm, "find_cli", lambda command: None)
    with pytest.raises(llm.LlmError, match="install Claude Code"):
        llm.start_login(CFG, popen)


# --------------------------------------------------------------------------- title text


@pytest.mark.parametrize("reply, expected", [
    ("Budgetgenomgång Q1", "Budgetgenomgång Q1"),
    ('"Planering av lobbydagar".', "Planering av lobbydagar"),
    ("**Titel: Styrelsemöte i november**", "Styrelsemöte i november"),
    ("# Title: Weekly sync - launch!\nHere is why I chose it", "Weekly sync - launch"),
    ("\n\n  “Kursplanering”  \n", "Kursplanering"),
    ("Budget: Q1/Q2 <draft>?", "Budget Q1 Q2 draft"),  # nothing a folder name cannot hold
    ("", None),
    ("   ", None),
    ("Meeting", None),
    ("Untitled meeting", None),
    ("ett två tre fyra fem sex sju åtta nio tio elva", None),  # rambling, not a title
])
def test_clean_title_reply(reply, expected):
    assert llm.clean_title_reply(reply) == expected


def test_a_long_title_is_cut_at_a_word():
    out = llm.clean_title_reply("Genomgång av " + "styrelsens " * 3 + "ekonomiska läge")
    assert out is not None and len(out) <= llm.MAX_TITLE_CHARS and not out.endswith(" ")
    assert llm.clean_title_reply("x" * 200) == "x" * llm.MAX_TITLE_CHARS


def seg(seq, text, speaker="Anna"):
    return Segment(seq, seq * 5.0, seq * 5.0 + 4, "2026-01-01T10:00:00", "mic", speaker, text,
                   "sv")


def test_the_title_sample_is_everything_when_short_and_capped_when_long():
    short = [seg(1, "hej"), seg(2, "tjena", "Per")]
    assert llm.sample_for_title(short) == "Anna: hej\nPer: tjena"
    long = [seg(i, f"rad {i} " + "ord " * 30) for i in range(1, 400)]
    text = llm.sample_for_title(long, cap=3000)
    assert len(text) <= 3000
    assert text.startswith("Anna: rad 1 ")  # the beginning ...
    assert "\n[...]\n" in text  # ... plus a piece from further in
    first_mid = int(text.split("\n[...]\n", 1)[1].split()[2])
    assert first_mid > 100


def test_suggest_title_sends_the_start_and_cleans_the_reply(tmp_path):
    session = make_session(tmp_path)
    rec = Recorder('"Rapport och budget".')
    assert llm.suggest_title(session, CFG, rec) == "Rapport och budget"
    assert "Alice: Jag skickar rapporten" in rec.calls[0][1]["input"]
    assert "title" in rec.system_prompts[0].lower() and "{language}" not in rec.system_prompts[0]
    assert rec.calls[0][0][rec.calls[0][0].index("--model") + 1] == "haiku"  # from [llm]


def test_suggest_title_refuses_tiny_or_unusable_cases(tmp_path):
    session = make_session(tmp_path)
    with pytest.raises(llm.LlmError, match="usable title"):
        llm.suggest_title(session, CFG, Recorder("Meeting"))
    w = SessionWriter.create(root=tmp_path / "tiny", sources=["mic"])
    w.append(t_start=0, t_end=1, source="mic", speaker="A", text="Hej", lang="sv")
    w.close()
    never = Recorder("x", raises=AssertionError("must not call Claude for so little text"))
    with pytest.raises(llm.LlmError, match="too little"):
        llm.suggest_title(w.dir, CFG, never)


# --------------------------------------------------------------------------- settings


def test_auto_title_setting_is_on_by_default_and_validated(make_ui):
    assert settings.load().auto_title is True
    assert settings.update({"auto_title": False}).auto_title is False
    assert settings.update({"auto_title": "no"}).auto_title is False  # bad value ignored
    _, c = make_ui()
    assert c.get("/api/settings")[1]["settings"]["auto_title"] is False
    assert c.post("/api/settings", {"auto_title": "yes"})[0] == 400
    assert c.post("/api/settings", {"auto_title": True})[1]["settings"]["auto_title"] is True


# --------------------------------------------------------------------------- API: status


class FakeProc:
    def __init__(self):
        self.done = threading.Event()

    def poll(self):
        return 0 if self.done.is_set() else None

    def wait(self):
        self.done.wait(10)


def test_llm_status_endpoint_is_cached_and_refreshable(make_ui):
    calls = []

    def probe():
        calls.append(1)
        return llm.ClaudeStatus("ok", method="claude.ai", plan="pro")

    _, c = make_ui(llm_probe=probe)
    res = c.get("/api/llm/status")[1]["llm"]
    assert res["state"] == "ok" and res["plan"] == "pro" and res["login_running"] is False
    c.get("/api/llm/status")
    assert len(calls) == 1
    c.get("/api/llm/status?refresh=1")
    assert len(calls) == 2
    assert c.get("/api/llm/status", token=None)[0] == 401


def test_a_job_failing_with_an_auth_error_refreshes_the_status(make_ui):
    state = ["ok"]
    calls = []

    def probe():
        calls.append(state[0])
        return llm.ClaudeStatus(state[0])

    def summary(session_dir, params, progress):
        state[0] = "logged_out"
        raise llm.LlmAuthError(llm.AUTH_MESSAGE)

    writer = make_ui_session("2026-01-01T10:00:00", LINES)
    _, c = make_ui(runners={"summary": summary}, llm_probe=probe)
    assert c.get("/api/llm/status")[1]["llm"]["state"] == "ok"
    c.post(f"/api/sessions/{writer.session_id}/jobs", {"kind": "summary"})
    job = wait_for(lambda: next((j for j in c.get("/api/jobs")[1]["jobs"]
                                 if j["state"] != "running"), None))
    assert job["state"] == "error" and job["error"] == llm.AUTH_MESSAGE
    # no 60 s wait: the next status request asks again and sees the new state
    assert c.get("/api/llm/status")[1]["llm"]["state"] == "logged_out"
    assert calls == ["ok", "logged_out"]


def test_login_endpoint_starts_one_console_and_refreshes_when_it_closes(make_ui):
    procs: list[FakeProc] = []
    state = ["logged_out"]

    def login(cfg):
        procs.append(FakeProc())
        return procs[-1]

    _, c = make_ui(llm_login=login, llm_probe=lambda: llm.ClaudeStatus(state[0]))
    assert c.get("/api/llm/status")[1]["llm"]["state"] == "logged_out"
    assert c.post("/api/llm/login")[1] == {"started": True, "running": True}
    assert c.get("/api/llm/status")[1]["llm"]["login_running"] is True
    assert c.post("/api/llm/login")[1]["started"] is False  # one console at a time
    assert len(procs) == 1
    state[0] = "ok"
    procs[0].done.set()  # the user finished (or closed) the console
    wait_for(lambda: c.get("/api/llm/status")[1]["llm"]["state"] == "ok")
    assert c.post("/api/llm/login")[1]["started"] is True  # may start again
    procs[1].done.set()


def test_login_without_the_cli_is_a_clear_conflict(make_ui):
    def login(cfg):
        raise llm.LlmError("cannot run 'claude': install Claude Code first")

    _, c = make_ui(llm_login=login)
    status, res = c.post("/api/llm/login")
    assert status == 409 and "install Claude Code" in res["error"]


def test_install_page_opens_the_fixed_address(make_ui):
    opened = []
    _, c = make_ui(open_url=opened.append)
    assert c.post("/api/llm/install", {"url": "https://evil.invalid"})[0] == 200
    assert opened == [llm.INSTALL_URL]  # never an address from the page


# --------------------------------------------------------------------------- API: auto title


def idle(c):
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "idle")


def record_once(c, body=None, *, toggle=None):
    """One short recording; returns the recorder snapshot taken just before Stop."""
    c.post("/api/record/start", {"mic": True, "loopback": False, **(body or {})})
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "recording")
    if toggle is not None:
        assert c.post("/api/record/auto_title", {"enabled": toggle})[0] == 200
    snap = c.get("/api/status")[1]["recorder"]
    c.post("/api/record/stop")
    idle(c)
    return snap


def fake_steps(ran, failing=()):
    def runner(kind):
        def run(session_dir, params, progress):
            ran.append(kind)
            if kind in failing:
                raise llm.LlmAuthError(llm.AUTH_MESSAGE)
            return kind
        return run

    return {k: runner(k) for k in ("finalize", "title", "clean", "summary", "todo")}


def auto_jobs(c, count=1):
    def done():
        jobs = [j for j in c.get("/api/jobs")[1]["jobs"] if j["kind"] == "auto"]
        return jobs if len(jobs) >= count and all(j["state"] != "running" for j in jobs) else None

    return wait_for(done)


def test_title_step_runs_after_finalize_and_before_the_rest_when_switched_on(make_ui):
    ran: list[str] = []
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()), runners=fake_steps(ran))
    c.post("/api/settings", {"auto_clean": True, "auto_summary": True})
    snap = record_once(c)  # no auto_title in the request: the setting (on) decides
    assert snap["auto_title"] is True
    auto_jobs(c)
    assert ran == ["finalize", "title", "clean", "summary"]


def test_switch_off_or_a_typed_title_means_no_title_step(make_ui, tmp_path):
    ran: list[str] = []
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()), runners=fake_steps(ran))
    pid = c.post("/api/projects", {"path": str(tmp_path), "name": "Proj",
                                   "create": True})[1]["project"]["id"]
    assert record_once(c, {"auto_title": False})["auto_title"] is False
    auto_jobs(c)
    assert ran == ["finalize"]

    # a typed title is never replaced, even with the switch on
    ran.clear()
    typed = record_once(c, {"project": pid, "title": "Styrelsemöte", "auto_title": True})
    assert typed["auto_title"] is False
    auto_jobs(c, 2)
    assert ran == ["finalize"]


def test_the_setting_is_only_the_default_for_the_switch(make_ui):
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()), runners=fake_steps([]))
    c.post("/api/settings", {"auto_title": False})
    assert record_once(c)["auto_title"] is False
    assert record_once(c, {"auto_title": True})["auto_title"] is True


def test_the_switch_can_be_changed_while_recording(make_ui):
    ran: list[str] = []
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()), runners=fake_steps(ran))
    assert c.post("/api/record/auto_title", {"enabled": True})[0] == 409  # not recording
    assert c.post("/api/record/start", {"auto_title": "yes"})[0] == 400
    assert record_once(c, {"auto_title": False}, toggle=True)["auto_title"] is True
    auto_jobs(c)
    assert ran == ["finalize", "title"]
    ran.clear()
    assert record_once(c, {"auto_title": True}, toggle=False)["auto_title"] is False
    auto_jobs(c, 2)
    assert ran == ["finalize"]


def test_bad_toggle_values_are_rejected(make_ui):
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()))
    c.post("/api/record/start", {"mic": True, "loopback": False})
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "recording")
    assert c.post("/api/record/auto_title", {"enabled": "yes"})[0] == 400
    assert c.post("/api/record/auto_title", {})[0] == 400
    c.post("/api/record/stop")
    idle(c)


def test_a_failing_title_is_quiet_and_does_not_stop_the_other_steps(make_ui):
    ran: list[str] = []
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()),
                   runners=fake_steps(ran, failing={"title"}))
    c.post("/api/settings", {"auto_summary": True})
    record_once(c)
    (job,) = auto_jobs(c)
    assert ran == ["finalize", "title", "summary"]
    assert job["state"] == "done" and job["error"] == ""
    assert "failed" not in job["message"] and "skipped: title" in job["message"]


def test_only_a_failing_title_is_not_an_error_either(make_ui):
    ran: list[str] = []
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()),
                   runners={"title": fake_steps(ran, failing={"title"})["title"]})
    record_once(c)
    (job,) = auto_jobs(c)
    assert ran == ["title"]
    assert job["state"] == "done" and job["error"] == ""


# --------------------------------------------------------------------------- the title runner


def project_meeting(tmp_path, title=None):
    p = projects.add_project(tmp_path, name="Proj", create=True)
    writer = make_ui_session("2026-01-01T10:00:00", LINES)
    return writer.dir, projects.create_meeting(p, writer.dir, title)


def title_runner():
    return default_runners(Config(), lambda: None)["title"]


def test_title_runner_names_an_untitled_meeting_and_its_folder(tmp_path, monkeypatch):
    session, m = project_meeting(tmp_path)
    assert m.title == projects.DEFAULT_TITLE
    asked = []

    def fake_run(system, text, cfg, runner=None):
        asked.append((cfg.model, text))
        return '"Planering av lobbydagar."'

    monkeypatch.setattr(llm, "run_claude", fake_run)
    msg = title_runner()(session, {}, lambda *a: None)
    assert msg == "titled: Planering av lobbydagar"
    assert projects.meeting_title(session) == "Planering av lobbydagar"
    folder = projects.meeting_folder(session)
    assert folder is not None and folder.name == "2026-01-01 Planering av lobbydagar"
    assert not m.folder.exists()  # the folder was renamed, not copied
    assert asked[0][0] == "haiku" and "Good morning" in asked[0][1]


def test_title_runner_never_overwrites_a_title_the_user_gave(tmp_path, monkeypatch):
    session, _ = project_meeting(tmp_path, title="Styrelsemöte")

    def forbidden(*a, **kw):
        raise AssertionError("Claude must not be asked for a meeting that has a title")

    monkeypatch.setattr(llm, "run_claude", forbidden)
    with pytest.raises(RuntimeError, match="already has a title"):
        title_runner()(session, {}, lambda *a: None)
    assert projects.meeting_title(session) == "Styrelsemöte"


def test_title_runner_skips_when_the_user_renames_while_claude_thinks(tmp_path, monkeypatch):
    session, _ = project_meeting(tmp_path)

    def fake_run(system, text, cfg, runner=None):
        projects.rename_meeting(session, "Mitt eget namn")  # the user was faster
        return "Automatisk titel"

    monkeypatch.setattr(llm, "run_claude", fake_run)
    with pytest.raises(RuntimeError, match="meanwhile"):
        title_runner()(session, {}, lambda *a: None)
    assert projects.meeting_title(session) == "Mitt eget namn"


def test_title_runner_works_in_the_inbox_and_leaves_it_untitled_on_failure(monkeypatch):
    writer = make_ui_session("2026-01-01T10:00:00", LINES)
    monkeypatch.setattr(llm, "run_claude", lambda *a, **kw: "Kort möte om listan")
    assert title_runner()(writer.dir, {}, lambda *a: None) == "titled: Kort möte om listan"
    assert projects.meeting_title(writer.dir) == "Kort möte om listan"

    other = make_ui_session("2026-01-02T10:00:00", LINES)

    def fail(*a, **kw):
        raise llm.LlmAuthError(llm.AUTH_MESSAGE)

    monkeypatch.setattr(llm, "run_claude", fail)
    with pytest.raises(llm.LlmAuthError):
        title_runner()(other.dir, {}, lambda *a: None)
    assert projects.meeting_title(other.dir) == projects.DEFAULT_TITLE
