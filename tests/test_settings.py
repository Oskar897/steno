"""App settings, the clean-up glossary and the automatic jobs after a recording."""

from __future__ import annotations

import time

from steno import clean, settings
from steno.config import LlmConfig
from tests.test_clean import make_session as make_clean_session
from tests.test_clean import reply
from tests.test_llm import Recorder

from .test_ui import burst_audio, fake_engine_factory, make_ui, wait_for

__all__ = ["make_ui"]  # fixture re-export


def test_settings_default_off_and_survive_bad_input():
    s = settings.load()
    assert not (s.auto_clean or s.auto_summary or s.auto_todo) and s.glossary == []
    s = settings.update({"auto_clean": True, "auto_todo": "yes", "glossary": "Jira, canvas;Jira\n",
                         "unknown": 1})
    assert s.auto_clean and not s.auto_todo  # "yes" is not a bool: ignored
    assert s.glossary == ["Jira", "canvas"] and settings.load() == s
    (settings.paths.data_dir() / settings.SETTINGS_NAME).write_text("{broken", encoding="utf-8")
    assert settings.load() == settings.AppSettings()


def test_glossary_goes_into_the_prompt_and_may_introduce_its_terms(tmp_path):
    lines = [("Anna", "kan du lägga in deadlinen i J här så att alla ser den")]
    session = make_clean_session(tmp_path, lines)
    edit = reply((1, "J här", "Jira"))
    without = clean.clean_session(session, LlmConfig(), dry_run=True, runner=Recorder(edit),
                                  glossary=[])
    assert without.meta["applied"] == 0  # a new name that is nowhere in the transcript
    rec = Recorder(edit)
    res = clean.clean_session(session, LlmConfig(), dry_run=True, runner=rec, glossary=["Jira"])
    assert res.meta["applied"] == 1 and "lägga in deadlinen i Jira så" in res.segments[0].text
    assert "- Jira" in rec.system_prompts[0]


def test_glossary_from_settings_is_the_default(tmp_path):
    settings.update({"glossary": ["Jira"]})
    session = make_clean_session(tmp_path, [("Anna", "lägg det i J här")])
    rec = Recorder(reply((1, "J här", "Jira")))
    assert clean.clean_session(session, LlmConfig(), dry_run=True, runner=rec).meta["applied"] == 1


def test_settings_api_and_automatic_jobs_after_stop(make_ui):
    ran: list[str] = []

    def runner(kind):
        def run(session_dir, params, progress):
            progress("working")
            ran.append(kind)
            if kind == "summary":
                raise RuntimeError("no Claude")
            return kind
        return run

    runners = {k: runner(k) for k in ("finalize", "clean", "summary", "todo")}
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()), runners=runners)
    assert c.get("/api/settings")[1]["settings"]["auto_clean"] is False
    assert c.post("/api/settings", {"auto_clean": "on"})[0] == 400
    s = c.post("/api/settings", {"auto_clean": True, "auto_summary": True, "auto_todo": True,
                                 "glossary": ["Canvas"]})[1]["settings"]
    assert s["glossary"] == ["Canvas"]
    assert c.post("/api/sessions/x/jobs", {"kind": "auto"})[0] in (400, 404)

    c.post("/api/record/start", {"mic": True, "loopback": False})
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "recording")
    time.sleep(0.3)
    c.post("/api/record/stop")
    job = wait_for(lambda: next((j for j in c.get("/api/jobs")[1]["jobs"]
                                 if j["kind"] == "auto" and j["state"] != "running"), None))
    # finalize first (on by default, no kept audio needed), then the switches, in order; a failing
    # step does not stop the rest
    assert ran == ["finalize", "clean", "summary", "todo"]
    assert job["state"] == "done" and "failed: summary" in job["message"]


def test_theme_and_transparency_settings(make_ui):
    s = settings.load()
    assert s.theme == "system" and s.transparent is True
    s = settings.update({"theme": "neon", "transparent": "no"})
    assert s.theme == "system" and s.transparent is True  # bad values ignored
    assert settings.update({"theme": "light", "transparent": False}).theme == "light"
    _, c = make_ui()
    assert c.post("/api/settings", {"theme": "neon"})[0] == 400
    assert settings.update({"palette": "wine"}).palette == "wine"
    assert settings.update({"palette": "neon"}).palette == "wine"  # bad value ignored
    assert c.post("/api/settings", {"palette": "neon"})[0] == 400
    assert settings.update({"intensity": "strong"}).intensity == "strong"
    assert settings.update({"intensity": "loud"}).intensity == "strong"  # bad value ignored
    assert c.post("/api/settings", {"intensity": "loud"})[0] == 400
    assert settings.load().ui_scale == 100
    assert settings.update({"ui_scale": 125}).ui_scale == 125
    assert settings.update({"ui_scale": 333}).ui_scale == 125
    assert settings.update({"ui_scale": True}).ui_scale == 125
    assert c.post("/api/settings", {"ui_scale": 333})[0] == 400
    assert c.post("/api/settings", {"transparent": 1})[0] == 400
    res = c.post("/api/settings", {"theme": "dark", "transparent": True})[1]["settings"]
    assert res["theme"] == "dark" and res["transparent"] is True
