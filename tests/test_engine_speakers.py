from __future__ import annotations

import json
from dataclasses import replace

import numpy as np

from steno import transcript
from steno.audio import ArraySource
from steno.config import Config, SpeakersConfig
from steno.engine import Engine
from steno.labels import SessionLabels, resolve_segments
from steno.profiles import ProfileStore, SessionEmbeddings

from .synth import EnergyVad, FakeAsr, FakeEmbedder, silence, timeline, voice_vector

A, B, C = 220.0, 330.0, 440.0


def pad(audio, seconds=1.5):
    return np.concatenate([audio, silence(seconds)])


def run(sources, config=None, embedder=None, **kw):
    embedder = embedder if embedder is not None else FakeEmbedder()
    engine = Engine(config or Config(), FakeAsr(), sources, vad_factory=EnergyVad,
                    embedder=embedder, **kw)
    seen = []
    engine.subscribe(seen.append)
    engine.start()
    assert engine.wait(timeout=20)
    engine.stop()
    return engine, embedder, seen


def meeting():
    """One mic, two fake voices alternating, plus a clip too short to embed."""
    return pad(timeline(
        (1.0, 2.5, 0.3, A), (4.0, 5.5, 0.3, B), (7.0, 8.5, 0.3, A), (10.0, 10.4, 0.3, B),
        (12.0, 13.5, 0.3, B),
    ))


def test_mic_segments_get_speaker_ids_not_the_fixed_me_label():
    engine, embedder, seen = run([ArraySource("mic", meeting())])
    segs = transcript.read_segments(engine.session_dir)
    assert [s.speaker_id for s in segs] == ["S1", "S2", "S1", "S1", "S2"] or \
        [s.speaker_id for s in segs][:3] == ["S1", "S2", "S1"]
    assert [s.speaker for s in segs][:3] == ["Speaker 1", "Speaker 2", "Speaker 1"]
    assert all(s.provisional for s in segs)
    assert all(s.speaker != "Me" for s in segs)
    assert segs[0].speaker_conf is not None
    # The 0.4 s clip (seq 4) is below min_embed_s: inherits S1 with zero confidence.
    assert segs[3].speaker_id == "S1" and segs[3].speaker_conf == 0.0
    assert len(embedder.calls) == 8  # two 1.5 s windows per embedded segment, none for seq 4
    assert seen == segs  # subscribers get the speaker fields too

    meta = json.loads((engine.session_dir / "session.json").read_text(encoding="utf-8"))
    assert meta["speakers"] == "fake-embedder"


def test_session_files_embeddings_and_speakers_json():
    engine, _, _ = run([ArraySource("mic", meeting())])
    d = engine.session_dir
    emb = SessionEmbeddings.load(d)
    assert sorted(emb) == [1, 2, 3, 5]  # seq 4 was too short
    assert emb[1] @ voice_vector(0) > 0.9 and emb[2] @ voice_vector(1) > 0.9
    assert not (d / "audio-mic.flac").exists()
    data = SessionLabels(d).load()
    assert set(data["clusters"]) == {"S1", "S2"}
    assert not (d / "labels.json").exists()  # only `steno refine` writes the overlay


def test_enrolled_person_is_recognised_and_unknown_is_clustered():
    store = ProfileStore()
    alice = store.create("Alice", np.vstack([voice_vector(0, 0.05, s) for s in range(4)]))
    engine, _, _ = run([ArraySource("mic", meeting())], profile_store=store)
    segs = transcript.read_segments(engine.session_dir)
    assert segs[0].speaker == "Alice" and segs[0].speaker_id == f"P:{alice.id}"
    assert segs[0].provisional is False and segs[0].speaker_conf > 0.9
    assert segs[1].speaker == "Speaker 1" and segs[1].speaker_id == "S1"  # Bob: not enrolled
    assert segs[2].speaker == "Alice"
    # Nothing was learned from the live stream.
    assert [p.name for p in store.list()] == ["Alice"]
    assert len(store.get(alice.id).embeddings) == 4
    names = SessionLabels(engine.session_dir).load()["profiles"]
    assert names == {alice.id: "Alice"}


def test_both_streams_share_clusters():
    mic = pad(timeline((1.0, 2.5, 0.3, A), (9.0, 10.5, 0.3, C)))
    loop = pad(timeline((4.0, 5.5, 0.3, B), (13.0, 14.5, 0.3, A)))
    engine, _, _ = run([ArraySource("mic", mic), ArraySource("loopback", loop)])
    segs = transcript.read_segments(engine.session_dir)
    assert [(s.source, s.speaker_id) for s in segs] == [
        ("mic", "S1"), ("loopback", "S2"), ("mic", "S3"), ("loopback", "S1"),
    ]


def test_disabled_speakers_use_fallback_labels_and_skip_embedding():
    cfg = replace(Config(), speakers=SpeakersConfig(enabled=False))
    engine, embedder, _ = run([ArraySource("mic", meeting())], cfg)
    segs = transcript.read_segments(engine.session_dir)
    assert {s.speaker for s in segs} == {"Me"}
    assert all(s.speaker_id is None and s.provisional is None for s in segs)
    assert embedder.calls == []
    line = (engine.session_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert "speaker_id" not in line and "provisional" not in line
    assert not (engine.session_dir / "embeddings.bin").exists()


def test_no_embedder_keeps_layer1_behaviour():
    engine = Engine(Config(), FakeAsr(), [ArraySource("mic", meeting())], vad_factory=EnergyVad)
    engine.start()
    engine.wait(20)
    engine.stop()
    segs = transcript.read_segments(engine.session_dir)
    assert {s.speaker for s in segs} == {"Me"} and segs[0].speaker_id is None


def test_explicit_speaker_label_bypasses_recognition():
    engine, embedder, _ = run([ArraySource("mic", meeting())], speaker_labels={"mic": "Anna"})
    segs = transcript.read_segments(engine.session_dir)
    assert {s.speaker for s in segs} == {"Anna"} and segs[0].speaker_id is None
    assert embedder.calls == []


def test_embedding_error_does_not_stop_transcription():
    class Flaky(FakeEmbedder):
        failures = 0

        def embed(self, audio):
            if self.failures < 2:  # both windows of the first segment
                self.failures += 1
                raise RuntimeError("boom")
            return super().embed(audio)

    engine, _, _ = run([ArraySource("mic", meeting())], embedder=Flaky())
    segs = transcript.read_segments(engine.session_dir)
    assert len(segs) == 5
    assert segs[0].speaker_id == "U"  # first embedding failed: unknown, still transcribed
    assert segs[1].speaker_id == "S1"


def test_resolved_view_after_session(tmp_path):
    engine, _, _ = run([ArraySource("mic", meeting())])
    SessionLabels(engine.session_dir).set_cluster_name("S2", "Bob", None)
    resolved = resolve_segments(engine.session_dir, transcript.read_segments(engine.session_dir))
    assert resolved[1].speaker == "Bob" and resolved[0].speaker == "Speaker 1"


def test_keep_audio_writes_one_flac_per_stream():
    import soundfile

    mic = pad(timeline((1.0, 2.5, 0.3, A)))
    loop = pad(timeline((1.0, 2.5, 0.3, B)))
    engine, _, _ = run([ArraySource("mic", mic), ArraySource("loopback", loop)], keep_audio=True)
    for name, source in (("mic", mic), ("loopback", loop)):
        path = engine.session_dir / f"audio-{name}.flac"
        data, rate = soundfile.read(path)
        assert rate == 16000 and abs(len(data) - len(source)) < 4000


# --------------------------------------------------------------------------- turns inside a segment


def interjection_audio():
    """One continuous VAD segment: voice A, a 2 s interjection by B, then A again."""
    return pad(timeline((1.0, 5.0, 0.3, A), (5.0, 7.0, 0.3, B), (7.0, 11.0, 0.3, A)))


def run_words(sources, **kw):
    from .synth import FakeWordAsr

    engine = Engine(Config(), FakeWordAsr(), sources, vad_factory=EnergyVad,
                    embedder=FakeEmbedder(), **kw)
    engine.start()
    assert engine.wait(timeout=20)
    engine.stop()
    return engine


def test_one_segment_is_split_into_one_line_per_speaker_turn():
    engine = run_words([ArraySource("mic", interjection_audio())])
    segs = transcript.read_segments(engine.session_dir)
    assert [s.speaker_id for s in segs] == ["S1", "S2", "S1"]
    assert [s.seq for s in segs] == [1, 2, 3]
    assert abs(segs[0].t_start - 0.8) < 0.1 and abs(segs[-1].t_end - 11.15) < 0.4  # VAD roll
    assert all(a.t_end <= b.t_start + 1e-6 for a, b in zip(segs, segs[1:], strict=False))
    words = [w for s in segs for w in s.text.split()]
    assert words == [f"w{i}" for i in range(len(words))]  # every word once, in order
    assert all(s.text for s in segs)
    # S2 only got the words around its 2 s turn
    assert 3 <= len(segs[1].text.split()) <= 12
    assert all(s.provisional for s in segs)
    # embeddings are stored per written line, windows once per window
    emb = SessionEmbeddings.load(engine.session_dir)
    assert sorted(emb) == [1, 2, 3]
    from steno.profiles import SessionWindows

    assert len(SessionWindows.load(engine.session_dir)) >= 10
    assert (engine.session_dir / "windows.bin").is_file()


def test_unconfirmed_interjection_speaker_is_folded_back_after_the_session():
    engine = run_words([ArraySource("mic", interjection_audio())])
    # 2 s of S2 in one segment is below the confirmation rule: merged into S1 at close
    labels = SessionLabels(engine.session_dir).load()
    assert labels["merged"] == {"S2": "S1"}
    resolved = resolve_segments(engine.session_dir, transcript.read_segments(engine.session_dir))
    assert {s.speaker for s in resolved} == {"Speaker 1"}


def test_backend_without_word_timestamps_keeps_one_line_per_segment():
    engine, _, _ = run([ArraySource("mic", interjection_audio())])  # plain FakeAsr
    segs = transcript.read_segments(engine.session_dir)
    assert len(segs) == 1 and segs[0].speaker_id == "S1"
    assert abs(segs[0].t_end - segs[0].t_start - 10.4) < 0.6  # the whole segment span


def test_forced_speaker_label_never_splits_turns():
    engine = run_words([ArraySource("mic", interjection_audio())], speaker_labels={"mic": "Anna"})
    segs = transcript.read_segments(engine.session_dir)
    assert len(segs) == 1 and segs[0].speaker == "Anna" and segs[0].speaker_id is None
