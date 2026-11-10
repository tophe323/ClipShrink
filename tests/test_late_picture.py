"""Files whose picture starts after the sound (some Webex and screen recordings).
The trial encode used to cut its samples before the picture began and failed with
"Stream map '' matches no streams"."""
from clipshrink import encode
from clipshrink.estimate import estimate_savings
from clipshrink.scan import probe_file
from helpers.media import ffmpeg, probe


def _late_clip(path, start=40, seconds=20):
    ffmpeg("-itsoffset", start, "-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={seconds}",
           "-f", "lavfi", "-i", f"sine=d={start + seconds + 20}", "-map", "0", "-map", "1",
           "-c:v", "libx264", "-g", "30", "-c:a", "aac", path)
    return path


def test_scan_notes_where_the_picture_starts(tmp_path):
    info = probe_file(_late_clip(tmp_path / "late.mp4"))
    assert 39.5 < info.video_start < 40.5
    assert 19.5 < info.duration < 20.5


def test_samples_are_cut_where_there_is_a_picture(tmp_path):
    info = probe_file(_late_clip(tmp_path / "late.mp4"))
    segs = encode.cut_segments(info.path, info, tmp_path)
    assert len(segs) == 3
    for at, seg in segs:
        assert 40 <= at <= 60
        assert any(s["codec_type"] == "video" for s in probe(seg)["streams"]), seg


def test_samples_without_a_picture_fall_back_to_the_whole_file(tmp_path):
    info = probe_file(_late_clip(tmp_path / "late.mp4"))
    info.video_start = 0.0          # as if the file gave no start time
    segs = encode.cut_segments(info.path, info, tmp_path)
    assert segs == [(0.0, tmp_path / "late.mp4")]


def test_trial_encode_works_on_a_late_picture(tmp_path):
    info = probe_file(_late_clip(tmp_path / "late.mp4"))
    est = estimate_savings([info], encoder="x265", work_dir=tmp_path / "work", ladder=[28])
    assert est.samples and all(s.encoded_bytes > 0 for s in est.samples)


def test_scan_reads_far_enough_to_describe_a_late_picture(tmp_path):
    info = probe_file(_late_clip(tmp_path / "late.mp4"))
    assert info.pix_fmt == "yuv420p"


def test_a_late_picture_converts_and_stays_in_sync(tmp_path):
    from clipshrink.engine import Converter
    src = _late_clip(tmp_path / "late.mp4")
    c = Converter(db_path=tmp_path / "s.db", work_dir=tmp_path / "t", encoder="x265")
    c.add_files([str(src)])
    c.run()
    rec = c.records()[0]
    assert rec.status == "verified", rec.reason
    streams = {s["codec_type"]: s for s in probe(rec.output)["streams"]}
    assert abs(float(streams["video"]["start_time"]) - 40) < 0.1
    assert abs(float(streams["audio"]["start_time"])) < 0.1
