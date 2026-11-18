from __future__ import annotations

import numpy as np

from steno import diarize as dz
from steno import segmentation as seg
from steno.config import SpeakersConfig
from steno.profiles import Profile
from steno.speakers import ProfileMatcher

from .synth import FakeEmbedder, timeline, voice_vector

A, B = 220.0, 330.0


class FakeSegmenter:
    """Segmentation model stand-in: powerset probabilities straight from known speaker turns."""

    def __init__(self, truth):
        self.truth = truth  # [(start_s, end_s, local speaker 0-2)]
        self.calls = 0

    def load(self) -> None:
        pass

    def run(self, audio, step_frames, progress=None):
        self.calls += 1
        n = len(audio)
        count = seg.window_count(n, step_frames)
        code_to_class = {0: 0, 1: 1, 2: 2, 4: 3, 3: 4, 5: 5, 6: 6}
        probs = np.full((count, seg.NUM_FRAMES, 7), 0.001, dtype=np.float32)
        for k in range(count):
            g = k * step_frames + np.arange(seg.NUM_FRAMES)
            t = (g * seg.FRAME_SHIFT + 495) / seg.SAMPLE_RATE
            code = np.zeros(seg.NUM_FRAMES, dtype=int)
            for a, b, s in self.truth:
                code |= ((t >= a) & (t < b)).astype(int) << s
            cls = np.array([code_to_class[int(c)] for c in code])
            probs[k, np.arange(seg.NUM_FRAMES), cls] = 0.994
        return seg.SegmentationResult(probs, step_frames, n)


def meeting_audio():
    """A talks 0-9 s, B talks 11-19 s, A joins B for a moment (14-16 s, overlap in the truth)."""
    return timeline((0.0, 9.0, 0.3, A), (11.0, 19.0, 0.3, B), (21.0, 28.0, 0.3, A))


TRUTH = [(0.0, 9.0, 0), (11.0, 19.0, 1), (14.0, 16.0, 0), (21.0, 28.0, 0)]


def covered(turns, a, b):
    """Fraction of [a, b] covered by the turns."""
    total = 0.0
    for t in turns:
        total += max(0.0, min(b, t.t_end) - max(a, t.t_start))
    return total / (b - a)


# --------------------------------------------------------------------------- helpers


def test_mask_runs_finds_true_runs():
    mask = np.array([0, 1, 1, 0, 1, 0, 0, 1, 1], dtype=bool)
    assert dz.mask_runs(mask) == [(1, 3), (4, 5), (7, 9)]
    assert dz.mask_runs(np.zeros(5, dtype=bool)) == []
    assert dz.mask_runs(np.ones(3, dtype=bool)) == [(0, 3)]


def test_frames_audio_concatenates_flagged_frames_and_cuts():
    audio = np.arange(40_000, dtype=np.float32)
    mask = np.zeros(100, dtype=bool)
    mask[10:12] = True
    mask[20:21] = True
    wave = dz.frames_audio(audio, 0, mask)
    assert len(wave) == 3 * seg.FRAME_SHIFT
    assert wave[0] == seg.frame_start_sample(10)
    assert len(dz.frames_audio(audio, 0, mask, max_s=0.01)) == 160
    assert len(dz.frames_audio(audio, 0, np.zeros(100, dtype=bool))) == 0


def test_local_masks_split_clean_from_overlapped_frames():
    hard = np.zeros((10, 3), dtype=bool)
    hard[0:6, 0] = True
    hard[4:9, 1] = True  # overlap with speaker 0 on frames 4-5
    out = {s: (active, clean) for s, active, clean in dz.local_masks(hard, 10)}
    assert set(out) == {0, 1}
    assert out[0][0].sum() == 6 and out[0][1].sum() == 4
    assert out[1][0].sum() == 5 and out[1][1].sum() == 3


def test_smooth_activity_fills_short_gaps_and_drops_short_runs():
    x = np.zeros(40, dtype=bool)
    x[2:10] = True
    x[12:20] = True  # a 2-frame gap
    x[30:31] = True  # a 1-frame blip
    out = dz.smooth_activity(x, min_on=3, min_off=3)
    assert dz.mask_runs(out) == [(2, 20)]


# --------------------------------------------------------------------------- linking


def make_stats(hard, embs):
    soft = hard.astype(np.float32)
    return dz.StreamStats("mic", 160_000, 148, soft, hard, embs)


def test_overlapping_local_speakers_never_share_a_cluster():
    hard = np.zeros((1, seg.NUM_FRAMES, 3), dtype=bool)
    hard[0, 0:300, 0] = True
    hard[0, 100:400, 1] = True  # overlaps speaker 0
    near_a = voice_vector(0, 0.02, 1)
    emb = [
        dz.LocalEmb(0, 0, near_a, 3.0, False),
        dz.LocalEmb(0, 1, voice_vector(0, 0.05, 2), 3.0, False),  # also sounds like cluster 0
    ]
    centroids = np.vstack([voice_vector(0), voice_vector(1)])
    stats = make_stats(hard, emb)
    params = dz.DiarizeParams()
    linked = dz.link_local_speakers(stats, centroids, params, constrain=True)
    assert linked[(0, 0)] != linked[(0, 1)]
    free = dz.link_local_speakers(stats, centroids, params, constrain=False)
    assert free[(0, 0)] == free[(0, 1)] == 0


def test_non_overlapping_local_speakers_may_share_a_cluster():
    hard = np.zeros((1, seg.NUM_FRAMES, 3), dtype=bool)
    hard[0, 0:100, 0] = True
    hard[0, 200:300, 1] = True  # the model split one person in two ids; they never overlap
    emb = [
        dz.LocalEmb(0, 0, voice_vector(0, 0.02, 1), 3.0, True),
        dz.LocalEmb(0, 1, voice_vector(0, 0.02, 2), 3.0, True),
    ]
    linked = dz.link_local_speakers(
        make_stats(hard, emb), np.vstack([voice_vector(0), voice_vector(1)]), dz.DiarizeParams()
    )
    assert linked[(0, 0)] == linked[(0, 1)] == 0


# --------------------------------------------------------------------------- stitching


def test_stitch_gives_two_speakers_on_overlap_frames_and_one_elsewhere():
    n_frames = seg.NUM_FRAMES
    soft = np.zeros((1, n_frames, 3), dtype=np.float32)
    soft[0, 0:400, 0] = 1.0
    soft[0, 200:300, 1] = 1.0  # speaker 1 only inside speaker 0's turn
    stats = dz.StreamStats("mic", 160_000, 148, soft, soft > 0.5)
    chosen = dz.stitch_stream(stats, {(0, 0): 0, (0, 1): 1}, 2, dz.DiarizeParams())
    assert chosen[100].tolist() == [True, False]
    assert chosen[250].tolist() == [True, True]  # overlap: both speakers
    assert chosen[500].tolist() == [False, False]


def test_stitch_does_not_add_a_dropped_speaker_to_the_count():
    soft = np.zeros((1, seg.NUM_FRAMES, 3), dtype=np.float32)
    soft[0, 0:200, 0] = 1.0
    soft[0, 0:200, 1] = 1.0  # two local speakers, but only one could be linked
    stats = dz.StreamStats("mic", 160_000, 148, soft, soft > 0.5)
    chosen = dz.stitch_stream(stats, {(0, 0): 0}, 2, dz.DiarizeParams())
    assert chosen[100].tolist() == [True, False]  # the unlinked one has no cluster to appear in


def test_speech_probability_decides_speech_and_the_summed_activity_decides_overlap():
    soft = np.zeros((1, seg.NUM_FRAMES, 3), dtype=np.float32)
    soft[0, 0:200, 0] = 0.45  # weak speech: P(speech) 0.45
    speech = np.zeros((1, seg.NUM_FRAMES), dtype=np.float32)
    speech[0, 0:200] = 0.45
    stats = dz.StreamStats("mic", 160_000, 148, soft, soft > 0.5, speech=speech)
    assign = {(0, 0): 0}
    strict = dz.stitch_stream(stats, assign, 1, dz.DiarizeParams(min_speech=0.5, min_act=0.1))
    loose = dz.stitch_stream(stats, assign, 1, dz.DiarizeParams(min_speech=0.4, min_act=0.1))
    assert not strict[100, 0] and loose[100, 0]


def test_activity_turns_convert_frames_to_seconds():
    chosen = np.zeros((300, 2), dtype=bool)
    chosen[100:200, 1] = True
    turns = dz.activity_turns(chosen, "mic", dz.DiarizeParams())
    assert len(turns) == 1 and turns[0].cluster == 1 and turns[0].stream == "mic"
    assert abs(turns[0].t_start - seg.frame_start_sample(100) / 16000) < 1e-9
    assert abs(turns[0].t_end - seg.frame_start_sample(200) / 16000) < 1e-9


# --------------------------------------------------------------------------- naming


def test_name_clusters_numbers_by_first_appearance_and_uses_profiles():
    turns = [
        dz.SpeakerTurn(5.0, 6.0, 0, "mic"),
        dz.SpeakerTurn(1.0, 2.0, 1, "mic"),
        dz.SpeakerTurn(3.0, 4.0, 2, "mic"),
    ]
    centroids = np.vstack([voice_vector(0), voice_vector(1), voice_vector(2)])
    ids, names = dz.name_clusters(turns, centroids, None)
    assert ids == ["S3", "S1", "S2"]
    profile = Profile("1a2b3c4d", "Alice", np.vstack([voice_vector(2)] * 5))
    ids, names = dz.name_clusters(turns, centroids, ProfileMatcher([profile], 0.55, 0.05))
    assert ids == ["S2", "S1", "P:1a2b3c4d"] and names == {"1a2b3c4d": "Alice"}


# --------------------------------------------------------------------------- end to end


def run_meeting(**kw):
    audio = meeting_audio()
    segmenter = FakeSegmenter(TRUTH)
    return dz.diarize(
        {"mic": audio}, segmenter, FakeEmbedder(noise=0.03), SpeakersConfig(), **kw
    )


def test_diarize_finds_two_speakers_with_the_right_turns():
    res = run_meeting()
    assert len(res.speaker_ids) == 2
    def cluster_at(sec):
        return next(t.cluster for t in res.turns if t.t_start <= sec <= t.t_end)

    a_id, b_id = cluster_at(1.0), cluster_at(12.5)
    assert a_id != b_id
    a_turns = [t for t in res.turns if t.cluster == a_id]
    b_turns = [t for t in res.turns if t.cluster == b_id]
    assert covered(a_turns, 0.5, 8.5) > 0.95
    assert covered(a_turns, 21.5, 27.5) > 0.95
    assert covered(b_turns, 11.5, 18.5) > 0.95
    assert covered(b_turns, 0.5, 8.5) < 0.05  # nobody confused with B early on


def test_diarize_keeps_the_overlap_as_two_simultaneous_speakers():
    res = run_meeting()
    in_overlap = [t for t in res.turns if t.t_start < 15.0 < t.t_end]
    assert len({t.cluster for t in in_overlap}) == 2


def test_diarize_honours_a_fixed_speaker_count_and_labels_by_stream():
    res = run_meeting(num_speakers=2)
    assert len(res.speaker_ids) == 2
    assert {s for _, _, _, s in res.labelled()} == {"mic"}
    assert all(sid.startswith("S") for _, _, sid, _ in res.labelled())


def test_diarize_of_silence_is_empty():
    audio = np.zeros(48_000, dtype=np.float32)
    res = dz.diarize({"mic": audio}, FakeSegmenter([]), FakeEmbedder(), SpeakersConfig())
    assert res.turns == [] and res.speaker_ids == []
