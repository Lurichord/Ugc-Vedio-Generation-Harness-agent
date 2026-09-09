from pathlib import Path

from ugc_harness.agents.voice_agent.audio import fit_wav_to_windows, inspect_wav
from ugc_harness.shared.media_duration import TimeWindow
from tests.test_voice import _wav_bytes


def test_time_window_scales_span_proportionally() -> None:
    window = TimeWindow(0, 1000, 0, 2000)
    assert window.map_ms(0) == 0
    assert window.map_ms(500) == 1000
    assert window.map_ms(1000) == 2000
    assert window.map_span(250, 750) == (500, 1500)


def test_fit_wav_to_windows_matches_target_duration(tmp_path: Path) -> None:
    source = tmp_path / "narration.wav"
    source.write_bytes(_wav_bytes(1000))
    output = tmp_path / "aligned.wav"
    info = fit_wav_to_windows(source, [(0, 400, 800), (400, 1000, 1200)], output)
    assert inspect_wav(output).duration_ms == 2000
    assert info.duration_ms == 2000
