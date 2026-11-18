from __future__ import annotations

import numpy as np

from steno import segmentation as seg


def test_powerset_matrix_matches_the_model_classes():
    mat = seg.powerset_matrix()
    assert mat.shape == (7, 3)
    assert mat[0].sum() == 0  # {}
    assert mat[1].tolist() == [1, 0, 0] and mat[3].tolist() == [0, 0, 1]
    assert mat[4].tolist() == [1, 1, 0] and mat[5].tolist() == [1, 0, 1]
    assert mat[6].tolist() == [0, 1, 1]


def one_hot(*classes: int) -> np.ndarray:
    probs = np.zeros((len(classes), 7), dtype=np.float32)
    probs[np.arange(len(classes)), classes] = 1.0
    return probs


def test_powerset_hard_decoding_per_frame():
    hard = seg.powerset_hard(one_hot(0, 1, 2, 4, 6))
    assert hard.tolist() == [
        [False, False, False],
        [True, False, False],
        [False, True, False],
        [True, True, False],
        [False, True, True],
    ]


def test_powerset_marginals_sum_classes_containing_the_speaker():
    probs = np.zeros((1, 7), dtype=np.float32)
    probs[0, 1] = 0.5  # {0}
    probs[0, 4] = 0.25  # {0,1}
    probs[0, 6] = 0.25  # {1,2}
    marg = seg.powerset_marginals(probs)[0]
    assert np.allclose(marg, [0.75, 0.5, 0.25])


def test_softmax_is_a_distribution_and_idempotent_on_probabilities():
    logits = np.array([[1.0, 2.0, 3.0, 0.0, -1.0, 0.5, 0.2]])
    p = seg.softmax(logits)
    assert np.isclose(p.sum(), 1.0)
    assert np.allclose(seg.softmax(np.log(p)), p, atol=1e-6)


def test_frame_grid_arithmetic():
    assert seg.total_frames(seg.WINDOW_SAMPLES) == seg.NUM_FRAMES  # 589 frames per window
    assert seg.total_frames(10) == 1
    assert seg.frame_start_sample(0) == 360 and seg.frame_start_sample(2) == 900
    # frames are contiguous: each starts where the previous one ends
    assert seg.frame_start_sample(5) - seg.frame_start_sample(4) == seg.FRAME_SHIFT
    assert abs(seg.frame_time(100) - (100 * 270 + 360) / 16000) < 1e-12


def test_step_is_quantised_to_whole_frames_and_clamped():
    assert seg.step_frames_for(2.5) == 148  # 39 960 samples = 2.4975 s
    assert seg.step_frames_for(0.1) == int(16000 / 270)  # never below ~1 s
    assert seg.step_frames_for(60.0) == seg.NUM_FRAMES  # never beyond one window


def test_window_count_covers_the_audio_with_a_padded_last_window():
    step = seg.step_frames_for(2.5)
    assert seg.window_count(1000, step) == 1
    assert seg.window_count(seg.WINDOW_SAMPLES, step) == 1
    n = 30 * 16000
    k = seg.window_count(n, step)
    assert (k - 1) * step * seg.FRAME_SHIFT + seg.WINDOW_SAMPLES >= n  # last window reaches the end
    assert (k - 2) * step * seg.FRAME_SHIFT + seg.WINDOW_SAMPLES < n  # and none is superfluous


def test_aggregate_windows_averages_overlapping_windows_on_the_global_grid():
    step, f = 100, seg.NUM_FRAMES
    values = np.zeros((3, f, 1), dtype=np.float32)
    values[0] = 1.0
    values[1] = 0.0
    values[2] = 1.0
    n_frames = 2 * step + f
    flat = np.ones(f, dtype=np.float32)
    num, den = seg.aggregate_windows(values, step, n_frames, weights=flat)
    mean = (num / den)[:, 0]
    assert mean[0] == 1.0  # only window 0 covers the first frames
    assert mean[step] == 0.5  # windows 0 and 1
    assert np.isclose(mean[2 * step], 2 / 3)  # windows 0, 1 and 2: values 1, 0, 1
    assert mean[-1] == 1.0  # only the last window covers the tail
    assert (den > 0).all()


def test_aggregate_windows_drops_frames_past_the_end_and_weights_edges_less():
    f = seg.NUM_FRAMES
    values = np.ones((1, f, 2), dtype=np.float32)
    num, den = seg.aggregate_windows(values, 50, n_frames=100)
    assert num.shape == (100, 2) and den.shape == (100, 1)
    w = seg.window_weights()
    assert w[0] < w[f // 2]  # Hamming: the edge counts less than the centre
