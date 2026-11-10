"""Sources that used to fail for reasons other than quality: .MTS camcorder files, and
phone videos whose encoder start delay made the container's duration look different."""
from pathlib import Path
from clipshrink import encode, scan
from clipshrink.engine import Converter
from helpers.media import ffmpeg, probe


def _mts(path):
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=6", "-f", "lavfi", "-i", "sine=d=6",
           "-c:v", "libx264", "-g", "25", "-c:a", "ac3", "-f", "mpegts", path)
    return path


def _mp4(path, seconds=3):
    """Generous bitrate, as a camera would use, so the conversion is smaller."""
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={seconds}", "-c:v", "libx264", "-crf", "8", "-g", "30",
           path)
    return path


def _run(src, tmp_path, **kw):
    c = Converter(db_path=tmp_path / "s.db", work_dir=tmp_path / "t", encoder="x265", **kw)
    c.add_files([str(src)])
    c.run()
    return c.record(str(src))


def test_mpegts_lists_its_video_twice_but_frames_count_once(tmp_path):
    src = _mts(tmp_path / "001.MTS")
    count, span = Converter._timeline(src)
    assert count == 150 and abs(span - 149 / 25) < 0.01


def test_an_mts_camcorder_file_converts(tmp_path):
    rec = _run(_mts(tmp_path / "001.MTS"), tmp_path)
    assert rec.status == "verified", rec.reason
    assert Converter._timeline(rec.output)[0] == 150


def test_a_different_container_duration_alone_does_not_reject(tmp_path, monkeypatch):
    """Phones: every frame keeps its time, but the encoder's start delay changes the
    duration ffprobe reports. The frames are what count."""
    src = _mp4(tmp_path / "phone.mp4")
    real = scan.probe_file

    def skewed(path):
        info = real(path)
        if "_(h265)" in Path(path).name or Path(path).name.startswith("built"):
            info.duration -= 0.1
        return info
    monkeypatch.setattr(scan, "probe_file", skewed)
    rec = _run(src, tmp_path)
    assert rec.status == "verified", rec.reason


def test_frames_that_change_time_are_still_caught(tmp_path, monkeypatch):
    src = _mp4(tmp_path / "a.mp4")
    real = encode.encode_video

    def stretched(s, out, enc, q, info, gate=None, on_time=None, target=None):
        if Path(s) != src:          # samples for the quality search stay untouched
            return real(s, out, enc, q, info, gate, on_time, target)
        return encode.run_ffmpeg(["-y", "-i", s, "-map", "0:v:0", "-vf", "setpts=PTS*1.1", "-fps_mode",
                                  "passthrough", *encode.encoder_args(enc, q, info), out], gate)
    monkeypatch.setattr(encode, "encode_video", stretched)
    rec = _run(src, tmp_path)
    assert rec.status == "kept_original" and "duration differs" in rec.reason
    assert not list(tmp_path.glob("*_(h265)*"))


def test_vmaf_failure_always_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(encode, "run_ffmpeg", lambda *a, **k: (3221225477, ""))
    info = scan.probe_file(_mp4(tmp_path / "a.mp4", 1))
    try:
        encode.measure_vmaf(tmp_path / "a.mp4", tmp_path / "a.mp4", info, tmp_path)
    except RuntimeError as ex:
        assert str(ex).endswith("FFmpeg stopped with code 0xc0000005")
    else:
        raise AssertionError("expected a failure")
