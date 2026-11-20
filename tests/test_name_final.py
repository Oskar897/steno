"""Naming a speaker of the final transcript (fake sessions, synthetic embeddings only)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from steno import labels, ops
from steno.profiles import ProfileStore, SessionWindows

from .test_ui import GatedRunner, make_session, make_ui, wait_for

__all__ = ["make_ui"]  # fixture re-export

# live: S1 holds both voices (the mistake finalize fixes), S2 only the second voice
LIVE = [
    (0.0, 3.0, "Speaker 1", "S1", "first voice"),
    (3.5, 7.0, "Speaker 1", "S1", "second voice, wrongly in S1"),
    (7.5, 11.0, "Speaker 2", "S2", "second voice again"),
]


def _final(writer):
    rows = [
        (0.0, 3.0, "S1", "Speaker 1"),
        (3.5, 11.0, "S2", "Speaker 2"),
    ]
    (writer.dir / "final.jsonl").write_text("\n".join(json.dumps({
        "v": 1, "seq": i + 1, "t_start": a, "t_end": b, "wall": "x", "source": "mic",
        "speaker": name, "text": "t", "lang": "sv", "speaker_id": sid, "final": True})
        for i, (a, b, sid, name) in enumerate(rows)) + "\n", encoding="utf-8")


def _windows(writer, rng):
    voice_a, voice_b = rng.normal(size=192), rng.normal(size=192)
    win = SessionWindows(writer.dir)
    for t in np.arange(0.0, 11.0, 0.5):
        if 3.0 < t < 3.5 or 7.0 < t < 7.5:
            continue
        v = (voice_a if t < 3.0 else voice_b) + rng.normal(scale=0.05, size=192)
        win.add("mic", float(t), float(t) + 1.5 if t + 1.5 <= 11.0 else 11.0,
                (v / np.linalg.norm(v)).astype(np.float32))
    win.close()
    return voice_a, voice_b


def test_name_final_speaker_goes_to_the_overlapping_live_cluster_and_learns_its_own_voice():
    rng = np.random.default_rng(1)
    writer = make_session("2026-01-01T10:00:00", LIVE)
    _final(writer)
    _, voice_b = _windows(writer, rng)
    store = ProfileStore()

    with pytest.raises(ops.OpsError):
        ops.name_final_speaker(writer.dir, "S9", "Nobody")
    res = ops.name_final_speaker(writer.dir, "S2", "Bertil")  # label only
    assert res.live_cluster == "S1" and res.profile is None  # S1 overlaps S2's 7.5 s turn most
    assert labels.SessionLabels(writer.dir).load()["clusters"]["S1"]["name"] == "Bertil"

    res = ops.name_final_speaker(writer.dir, "S2", "Bertil", store, save_profile=True)
    assert res.profile is not None and res.created and res.windows >= 3
    centroid = res.profile.centroid()
    unit_b = voice_b / np.linalg.norm(voice_b)
    assert float(centroid @ unit_b) > 0.9  # learned from the final turns: only the second voice


def test_naming_a_final_speaker_in_the_app_runs_finalize_again(make_ui):
    writer = make_session("2026-01-01T10:00:00", LIVE)
    _final(writer)
    gate = GatedRunner()
    gate.release.set()
    _, c = make_ui(runners={"finalize": gate, "summary": GatedRunner(), "todo": GatedRunner()})
    sid = writer.dir.name
    d = c.get(f"/api/sessions/{sid}")[1]
    assert d["speaker_space"] == "final"
    status, res = c.post(f"/api/sessions/{sid}/name", {"speaker": "S2", "name": "Bertil",
                                                       "save_profile": False, "view": "final"})
    assert status == 200 and "Bertil" in res["message"]
    wait_for(lambda: gate.calls)
    assert gate.calls[0][0] == sid
    assert c.post(f"/api/sessions/{sid}/name", {"speaker": "S7", "name": "X",
                                                "view": "final"})[0] == 400
