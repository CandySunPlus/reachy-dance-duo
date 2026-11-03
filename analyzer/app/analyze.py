"""librosa-based song analysis.

Direct port of reachy_dance_duo_source/reachy_dance_duo/behaviors/connected_choreographer.py:
- load_audio_av (lines 587-635)
- SongAnalyzer.analyze (lines 650-725)
- _compute_beat_energy (lines 727-750)
- _assign_sequences (lines 752-769)

Returns the same shapes the original ConnectedChoreographer consumes, so the
JS Beat Bandit mode can drive 8-beat blocks of choreography from the
sequence_assignments list.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import av  # type: ignore
import librosa  # type: ignore
import numpy as np

logger = logging.getLogger(__name__)

ANALYSIS_SR = 11025
HOP_LENGTH = 512
HIGH_THRESHOLD = 0.65
LOW_THRESHOLD = 0.35


def _load_audio_av(file_path: str | Path, target_sr: int = ANALYSIS_SR) -> tuple[np.ndarray, int]:
    container = av.open(str(file_path))
    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="flt", layout="mono", rate=target_sr)

    frames: list[Any] = []
    for frame in container.decode(stream):
        frame.pts = None
        frames.extend(resampler.resample(frame))

    chunks: list[np.ndarray] = []
    for frame in frames:
        arr = frame.to_ndarray()
        chunks.append(arr[0])

    if not chunks:
        return np.array([], dtype=np.float32), target_sr
    y = np.concatenate(chunks).astype(np.float32)
    return y, target_sr


def _beat_energy(beats_t: np.ndarray, rms: np.ndarray, sr: int) -> np.ndarray:
    rms_t = np.arange(len(rms)) * HOP_LENGTH / sr
    energy_at_beats = np.interp(beats_t, rms_t, rms)
    p10 = float(np.percentile(energy_at_beats, 10))
    p90 = float(np.percentile(energy_at_beats, 90))
    if p90 - p10 > 0:
        return np.clip((energy_at_beats - p10) / (p90 - p10), 0.0, 1.0).astype(np.float32)
    return (np.ones_like(energy_at_beats) * 0.5).astype(np.float32)


def _assign_sequences(energy_per_beat: np.ndarray) -> list[str]:
    out: list[str] = []
    for i in range(0, len(energy_per_beat), 8):
        avg = float(np.mean(energy_per_beat[i : i + 8])) if energy_per_beat[i : i + 8].size else 0.0
        if avg >= HIGH_THRESHOLD:
            out.append("high")
        elif avg <= LOW_THRESHOLD:
            out.append("low")
        else:
            out.append("medium")
    return out


def analyze(audio_path: str | Path) -> dict[str, Any]:
    y, sr = _load_audio_av(audio_path, target_sr=ANALYSIS_SR)
    if y.size == 0:
        raise RuntimeError(f"empty audio: {audio_path}")

    duration = float(len(y) / sr)
    if duration < 2.0:
        raise ValueError(
            f"audio too short ({duration:.1f}s) — need at least ~2s of music"
        )
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    tempo_val = float(tempo[0] if isinstance(tempo, np.ndarray) else tempo)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).astype(np.float64)
    if beat_times.size == 0:
        # Silence (or a drone with no onsets) leaves every downstream step
        # indexing an empty array; say so instead of a 500 from numpy.
        raise ValueError(
            "no beats detected — the audio looks silent or has no rhythm"
        )

    # Extend beats if detection stopped early
    if len(beat_times) > 1 and beat_times[-1] < duration - 1.0:
        avg_interval = float(np.mean(np.diff(beat_times[-10:])))
        extended: list[float] = []
        cur = float(beat_times[-1])
        while cur + avg_interval < duration:
            cur += avg_interval
            extended.append(cur)
        if extended:
            beat_times = np.concatenate([beat_times, np.array(extended)])

    rms = librosa.feature.rms(y=y, hop_length=HOP_LENGTH)[0]
    energy_per_beat = _beat_energy(beat_times, rms, sr)
    sequence_assignments = _assign_sequences(energy_per_beat)

    # The continuous RMS envelope drives Beat Bandit's antenna behavior in
    # the JS port (mirrors connected_choreographer._get_continuous_antennas
    # at lines 1274-1310). Keep envelope_sr == sr and hop_length == 512 so
    # the JS can index it as: idx = current_time * envelope_sr / hop_length.
    return {
        "duration_sec": duration,
        "tempo": tempo_val,
        "beats": [float(t) for t in beat_times],
        "energy_per_beat": [float(e) for e in energy_per_beat],
        "sequence_assignments": sequence_assignments,
        "energy_envelope": [float(e) for e in rms],
        "envelope_sr": int(sr),
        "hop_length": HOP_LENGTH,
    }
