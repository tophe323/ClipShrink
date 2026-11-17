"""ClipShrink 1.8: older camera formats (DV, Motion JPEG), a way to skip the test encodes,
and a time estimate that separates FFmpeg's start-up from its steady speed."""
import time
from pathlib import Path
import pytest
from helpers.media import ffmpeg, probe


def _dv(path, seconds=4):
    """PAL DV in AVI, interlaced bottom field first like a camcorder's, with 48 kHz PCM sound."""
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=720x576:r=25:d={seconds}", "-f", "lavfi", "-i",
           f"sine=d={seconds}:sample_rate=48000", "-vf", "setfield=bff", "-c:v", "dvvideo", "-pix_fmt", "yuv420p",
           "-c:a", "pcm_s16le", "-ac", "2", path)
    return path


def _mjpeg(path, seconds=4):
    """Motion JPEG as old compact cameras wrote it: full-range 4:2:2, 8-bit unsigned PCM sound."""
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=640x480:r=30:d={seconds}", "-f", "lavfi", "-i",
           f"sine=d={seconds}:sample_rate=11025", "-c:v", "mjpeg", "-q:v", "3", "-pix_fmt", "yuvj422p",
           "-c:a", "pcm_u8" if Path(path).suffix.lower() == ".avi" else "pcm_s16le", path)
    return path


def _convert(src, tmp_path, encoder="x265"):
    from clipshrink.engine import Converter
    c = Converter(db_path=tmp_path / "s.db", work_dir=tmp_path / "t", encoder=encoder)
    c.add_files([str(src)])
    c.run()
    return c.record(str(src))


def _mean_rgb(path, frame):
    import re, subprocess
    from clipshrink import tools
    r = subprocess.run([tools.ffmpeg(), "-hide_banner", "-i", str(path), "-vf",
                        f"select=eq(n\\,{frame}),format=gbrp,signalstats,metadata=print", "-frames:v", "1", "-f", "null", "-"],
                       capture_output=True, text=True)
    return [float(v) for v in re.findall(r"signalstats\.[YUV]AVG=([\d.]+)", r.stderr)]


# --- Scan
def test_dv_and_motion_jpeg_are_listed_to_convert(tmp_path):
    from clipshrink.scan import scan_folder
    _dv(tmp_path / "a.avi", 1)
    _mjpeg(tmp_path / "b.avi", 1)
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:d=1", "-c:v", "mpeg4", tmp_path / "c.avi")
    res = scan_folder(tmp_path)
    assert sorted(Path(f.path).name for f in res.h264) == ["a.avi", "b.avi"]
    assert [f.codec for f in res.other] == ["mpeg4"]


def test_raw_dv_files_are_found_too(tmp_path):
    from clipshrink.scan import scan_folder
    _dv(tmp_path / "a.avi", 1)
    ffmpeg("-i", tmp_path / "a.avi", "-c", "copy", "-f", "dv", tmp_path / "tape.dv")
    assert "tape.dv" in [Path(f.path).name for f in scan_folder(tmp_path).h264]


def test_dv_is_deinterlaced_even_when_the_file_doesnt_say(tmp_path):
    from clipshrink.scan import probe_file
    info = probe_file(_dv(tmp_path / "a.avi", 1))
    assert info.field_order in ("", "unknown") and info.legacy and info.deinterlace


def test_motion_jpeg_is_only_deinterlaced_when_it_says_so(tmp_path):
    from clipshrink.scan import probe_file
    info = probe_file(_mjpeg(tmp_path / "a.avi", 1))
    assert info.legacy and not info.deinterlace
    info.field_order = "tt"
    assert info.deinterlace


def test_h264_is_not_treated_as_an_old_format(tmp_path):
    from clipshrink.scan import probe_file
    from clipshrink import encode
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:d=1", "-c:v", "libx264", tmp_path / "a.mp4")
    info = probe_file(tmp_path / "a.mp4")
    assert not info.legacy and not info.deinterlace and encode.picture_filters(info) == []


# --- Conversion
def test_dv_becomes_one_progressive_frame_per_field(tmp_path):
    rec = _convert(_dv(tmp_path / "a.avi"), tmp_path)
    assert rec.status == "verified", rec.reason
    out = Path(rec.output)
    assert out.name == "a_(h265).mkv"
    s = {x["codec_type"]: x for x in probe(out)["streams"]}
    assert s["video"]["codec_name"] == "hevc" and s["video"]["pix_fmt"] == "yuv420p"
    assert s["video"]["avg_frame_rate"] == "50/1"
    assert s["audio"]["codec_name"] == "pcm_s16le" and s["audio"]["sample_rate"] == "48000"
    from clipshrink.engine import Converter
    count, span = Converter._timeline(out)
    assert count == 200 and abs(span - 199 / 50) < 0.005
    assert rec.output_size < rec.source_size / 3


def test_motion_jpeg_avi_converts_with_true_colours(tmp_path):
    src = _mjpeg(tmp_path / "MVI_0001.AVI")
    rec = _convert(src, tmp_path)
    assert rec.status == "verified", rec.reason
    s = {x["codec_type"]: x for x in probe(rec.output)["streams"]}
    assert s["video"]["color_range"] == "tv" and s["audio"]["codec_name"] == "pcm_u8"
    # full range in, limited range out: the picture must look the same, not washed out
    before, after = _mean_rgb(src, 60), _mean_rgb(rec.output, 60)
    assert len(before) == 3 and all(abs(a - b) < 4 for a, b in zip(before, after)), (before, after)


def test_motion_jpeg_mov_is_rewritten_not_grafted(tmp_path):
    rec = _convert(_mjpeg(tmp_path / "MVI_0002.MOV"), tmp_path)
    assert rec.status == "verified", rec.reason
    assert Path(rec.output).suffix == ".MOV"
    assert [x["codec_name"] for x in probe(rec.output)["streams"]] == ["hevc", "pcm_s16le"]


def test_vmaf_pairs_frames_whose_times_were_rounded(tmp_path):
    """Matroska keeps times to the millisecond: 0.033 must still pair with 0.0333, not with frame 0."""
    from clipshrink import encode
    from clipshrink.scan import probe_file
    src = _mjpeg(tmp_path / "a.avi", 2)
    out = tmp_path / "a.mkv"
    ffmpeg("-i", src, "-an", "-vf", "scale=out_range=tv,format=yuv420p", "-c:v", "libx264", "-crf", "10", out)
    s = encode.measure_vmaf(out, src, probe_file(src), tmp_path)
    assert s.frames == 60 and s.mean > 95


def test_a_format_it_doesnt_convert_is_refused_with_its_name(tmp_path):
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:d=1", "-c:v", "mpeg4", tmp_path / "c.avi")
    rec = _convert(tmp_path / "c.avi", tmp_path)
    assert rec.status == "kept_original"
    assert rec.reason == "This is mpeg4, which this tool doesn't convert, so it was left alone."
    assert rec.encode_runs == 0


# --- Skip the test encodes
@pytest.fixture
def win(workspace, qapp_instance):
    from clipshrink.ui import MainWindow
    w = MainWindow(db_path=workspace / "work" / "s.db", work_dir=workspace / "work" / "t")
    yield w
    w.busy = False
    w.close()


def test_skip_the_test_starts_converting_at_once(win, workspace, qapp_instance):
    from clipshrink.scan import scan_folder
    _mjpeg(workspace / "videos" / "a.avi", 2)
    assert not win.skip_test.isEnabled()
    win.folder = str(workspace / "videos")
    win._show_scan(scan_folder(workspace / "videos"))
    assert win.skip_test.isEnabled() and win.estimate is None
    win.skip_test.click()
    assert win.stack.currentIndex() == 2 and win.busy
    assert "no time estimate" in win.conv_info.text()
    end = time.time() + 180
    while win.busy and time.time() < end:
        qapp_instance.processEvents()
        time.sleep(0.01)
    assert not win.busy and win.conv_cards["Files Processed"].text() == "1 / 1"


def test_nothing_ticked_means_nothing_to_skip_to(win, workspace):
    from clipshrink.scan import scan_folder
    from PySide6.QtCore import Qt
    _mjpeg(workspace / "videos" / "a.avi", 1)
    win.folder = str(workspace / "videos")
    win._show_scan(scan_folder(workspace / "videos"))
    win.scan_table.item(0, 0).setCheckState(Qt.Unchecked)
    assert not win.skip_test.isEnabled()


def test_the_skip_button_is_translated(win):
    from clipshrink import i18n
    i18n.set_language("fr")
    try:
        win.retranslate()
        assert win.skip_test.text() == "Passer le test, convertir maintenant"
    finally:
        i18n.set_language("en")
        win.retranslate()


# --- Time estimate
def test_each_ffmpeg_run_reports_start_up_and_steady_speed(tmp_path):
    from clipshrink import encode
    code, _ = encode.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", "-c:v", "libx264",
                                 "-preset", "veryslow", "-f", "null", "-"])
    run = encode.last_run()
    assert code == 0 and run.total > 0 and 0 <= run.startup < run.total
    assert run.media > 3 and run.steady_rate and run.steady_rate > 0


def _timing(group_file, rungs, startup, rate_per_mpx_second):
    from clipshrink import encode
    from clipshrink.encode import Run, SampleRow, Score, SearchResult
    mpx = group_file.width * group_file.height / 1e6
    half = rate_per_mpx_second * mpx / 2
    row = SampleRow(0, Path("seg.mkv"), 1, Score(96, 92, 120), 0, 0,
                    Run(startup / 2, 4 * half, 4.0, 0), Run(startup / 2, 4 * half, 4.0, 0))
    return group_file, SearchResult(0, True, {i: [row, row, row] for i in range(rungs)})


def test_estimate_adds_start_up_per_run_and_speed_per_second():
    from clipshrink.estimate import conversion_seconds
    from clipshrink.scan import MediaInfo
    f = MediaInfo("a.mp4", 1, 0, "h264", 1920, 1080, 30, 600, 18000, 1)
    # 2 rungs of 3 samples, 1 s to start each run pair, 0.5 s per second of 1080p video
    t = conversion_seconds([f], [_timing(f, 2, 1.0, 0.5 / (1920 * 1080 / 1e6))])
    search = 2 * 3 * (1.0 + 4 * 0.5)
    whole = 1.0 + 600 * 0.5
    assert t == pytest.approx(search + whole)


def test_estimate_uses_each_picture_sizes_own_speed():
    from clipshrink.estimate import conversion_seconds
    from clipshrink.scan import MediaInfo
    hd = MediaInfo("a.mp4", 1, 0, "h264", 1920, 1080, 30, 100, 3000, 1)
    uhd = MediaInfo("b.mp4", 1, 0, "h264", 3840, 2160, 30, 100, 3000, 1)
    slow_4k = conversion_seconds([uhd], [_timing(hd, 1, 0, 0.1), _timing(uhd, 1, 0, 0.9)])
    assert slow_4k == pytest.approx(3 * 4 * 0.9 * 8.2944 + 100 * 0.9 * 8.2944)


def test_a_short_file_is_searched_whole():
    from clipshrink.estimate import conversion_seconds
    from clipshrink.scan import MediaInfo
    f = MediaInfo("a.mp4", 1, 0, "h264", 1000, 1000, 30, 10, 300, 1)
    t = conversion_seconds([f], [_timing(f, 3, 2.0, 1.0)])
    assert t == pytest.approx(3 * 1 * (2.0 + 10 * 1.0) + (2.0 + 10 * 1.0))


def test_version_is_1_8_2():
    from clipshrink import config
    root = Path(__file__).resolve().parents[1]
    iss = (root / "installer" / "clipshrink.iss").read_text(encoding="utf-8-sig")
    assert config.VERSION == "1.8.2" and "AppVersion=1.8.2\n" in iss


# --- 1080i camcorder H.264 (AVCHD)
class _Probe:
    def __init__(self, out):
        self.returncode, self.stdout, self.stderr = 0, out, ""


def _fake_packets(monkeypatch, lines, start):
    """What ffprobe reports: packets in file order as "pts,flags", and the track's start time."""
    import json
    from clipshrink import encode
    packets = [{"pts_time": p.split(",")[0], "flags": p.split(",")[1]} for p in lines]
    out = json.dumps({"packets": packets, "streams": [{"start_time": f"{start:.6f}"}]})
    monkeypatch.setattr(encode.tools, "run", lambda *a, **k: _Probe(out))


def test_one_packet_per_field_counts_as_one_frame(monkeypatch):
    """1080i camcorders store each field as its own packet, 0.02 s apart at 25 fps."""
    from clipshrink import encode
    fields = [f"{2.356 + i * 0.02:.3f},K__" if i == 0 else f"{2.356 + i * 0.02:.3f},___" for i in range(100)]
    _fake_packets(monkeypatch, fields, 2.356)
    tl = encode.timeline("x.mts", 25)
    assert tl.frames == 50 and abs(tl.span - 49 * 0.04) < 1e-6 and tl.lead == 0
    assert encode.timeline("x.mts").frames == 100        # without the frame rate, nothing is assumed


def test_frames_before_the_track_start_are_left_out(monkeypatch):
    """Leading frames of a group of pictures that began before the recording: timed before
    the track's start, skipped by players and by FFmpeg's conversion."""
    from clipshrink import encode
    frames = ["2.356,K__", "2.276,___", "2.316,___"] + [f"{2.396 + i * 0.04:.3f},___" for i in range(47)]
    _fake_packets(monkeypatch, frames, 2.356)
    tl = encode.timeline("x.mts", 25)
    assert tl.frames == 48 and tl.lead == 2 and abs(tl.span - 47 * 0.04) < 1e-6


def test_a_b_frame_shown_before_the_first_packet_still_counts(monkeypatch):
    """In MP4 the first packet is often a keyframe shown after the next B-frame. That B-frame
    is inside the track, which starts at the earliest frame shown."""
    from clipshrink import encode
    _fake_packets(monkeypatch, ["0.067,K__", "0.000,___", "0.033,___", "0.133,___", "0.100,___"], 0.0)
    tl = encode.timeline("x.mp4", 30)
    assert tl.frames == 5 and tl.lead == 0


def test_vmaf_skips_the_originals_leading_frames(tmp_path, monkeypatch):
    """Without the skip, every frame is compared with the one two frames away."""
    from clipshrink import encode
    from clipshrink.scan import probe_file
    clip = tmp_path / "clip.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=3", "-c:v", "libx264", "-crf", "10", clip)
    ref = tmp_path / "ref.mp4"      # the same clip with 2 different frames in front
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=320x240:r=25:d=0.08", "-i", clip, "-filter_complex",
           "[0:v][1:v]concat=n=2:v=1", "-c:v", "libx264", "-crf", "10", ref)
    real = encode.timeline
    monkeypatch.setattr(encode, "timeline", lambda p, fps=None: encode.Timeline(75, 2.96, 2) if p == ref else real(p, fps))
    s = encode.measure_vmaf(clip, ref, probe_file(clip), tmp_path)
    assert s.frames == 75 and s.mean > 95


def _interlaced_h264(path, seconds=3):
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=720x576:r=25:d={seconds}", "-vf", "setfield=tff", "-c:v", "libx264",
           "-crf", "8", "-flags", "+ildct+ilme", "-x264-params", "tff=1", path)
    return path


def test_interlaced_h264_says_so_and_is_deinterlaced(tmp_path):
    from clipshrink.scan import probe_file
    info = probe_file(_interlaced_h264(tmp_path / "a.mp4", 1))
    assert info.codec == "h264" and info.field_order in ("tt", "tb") and info.deinterlace and not info.legacy


def test_interlaced_h264_becomes_50_progressive_frames_a_second(tmp_path):
    rec = _convert(_interlaced_h264(tmp_path / "a.mp4"), tmp_path)
    assert rec.status == "verified", rec.reason
    s = {x["codec_type"]: x for x in probe(rec.output)["streams"]}
    assert s["video"]["avg_frame_rate"] == "50/1" and s["video"].get("field_order", "progressive") == "progressive"
    from clipshrink.engine import Converter
    assert Converter._timeline(rec.output)[0] == 150


def test_progressive_h264_is_left_at_its_frame_rate(tmp_path):
    from clipshrink.scan import probe_file
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=1", "-c:v", "libx264", tmp_path / "a.mp4")
    assert not probe_file(tmp_path / "a.mp4").deinterlace
