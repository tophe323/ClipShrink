"""The ClipShrink label inside every conversion: what made it, with which settings, how it scored
and what it saved. A scan uses it to tell a conversion apart and to offer a redo when the settings
in use now are different."""
import shutil
from pathlib import Path
import pytest
from helpers.media import ffmpeg


def _label(**kw):
    from clipshrink import label
    base = dict(version="9.9", codec="h265", encoder="x265", quality="24", target_mean=95.0, target_p1=90.0,
                vmaf_mean=96.123, vmaf_p1=92.4, original_bytes=1000, saved_bytes=700, date="2026-11-22",
                original="GX010001.MP4")
    base.update(kw)
    return label.make(label.Label(**base))


def _hevc(path, seconds=1, size="320x240"):
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s={size}:d={seconds}", "-c:v", "libx265", "-preset", "ultrafast",
           "-tag:v", "hvc1", path)
    return path


# --- the text
def test_the_label_reads_back_whatever_the_original_is_called():
    from clipshrink import label
    lab = label.parse(_label(original="a; b=c; weird.MP4"))
    assert (lab.version, lab.codec, lab.target, lab.quality) == ("9.9", "h265", (95.0, 90.0), "24")
    assert (lab.vmaf_mean, lab.vmaf_p1, lab.saved_bytes) == (96.12, 92.4, 700)
    assert lab.original == "a; b=c; weird.MP4"


def test_other_comments_are_not_taken_for_a_label():
    from clipshrink import label
    for text in ("", "Shot on my phone", "ClipShrink is great", None):
        assert label.parse(text) is None


# --- writing it
def test_taking_the_label_out_gives_back_the_camera_info_exactly(get_clip, workspace):
    from clipshrink import label, mp4graft
    own = workspace / "own.mp4"
    ffmpeg("-i", _hevc(workspace / "a.mp4"), "-c", "copy", "-metadata", "title=Trip", own)   # has its own list
    for src in (get_clip("gopro_like_1080p_telemetry"), own, workspace / "a.mp4"):
        u = mp4graft.udta(src)
        with_it = label._with_label(u, _label())
        assert with_it != u and label.without_label(with_it) == u
    assert label.without_label(label._with_label(b"", _label())) == b""


def test_an_mp4_label_keeps_every_track_and_the_camera_info(get_clip, workspace):
    from clipshrink import label, mp4graft
    src = get_clip("gopro_like_1080p_telemetry")
    out = workspace / "l.mp4"
    assert label.write(src, out, _label())
    assert mp4graft.nonvideo_digests(src, skip_video=False) == mp4graft.nonvideo_digests(out, skip_video=False)
    assert label.read(out).target == (95.0, 90.0)
    assert label._with_label(mp4graft.udta(src), _label()) == mp4graft.udta(out)
    r = ffmpeg_decode(out)
    assert r.returncode == 0 and not r.stderr.strip(), r.stderr[-500:]


def ffmpeg_decode(path):
    import subprocess
    from clipshrink import tools
    return subprocess.run([tools.ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v", "-map", "0:a?", "-f", "null",
                           "-"], capture_output=True, text=True)


def test_an_index_at_the_end_of_the_file_works_too(workspace):
    from clipshrink import label, mp4graft
    src = _hevc(workspace / "end.mp4")      # FFmpeg puts the index after the picture
    boxes = [k for k, _, _ in label._top_boxes(src)]
    assert boxes.index(b"moov") > boxes.index(b"mdat")
    out = workspace / "end_l.mp4"
    assert label.write(src, out, _label())
    assert mp4graft.nonvideo_digests(src, skip_video=False) == mp4graft.nonvideo_digests(out, skip_video=False)
    assert label.read(out) is not None and ffmpeg_decode(out).returncode == 0


def test_a_second_label_replaces_the_first(workspace):
    from clipshrink import label
    src = _hevc(workspace / "a.mp4")
    assert label.write(src, workspace / "b.mp4", _label(quality="24"))
    assert label.write(workspace / "b.mp4", workspace / "c.mp4", _label(quality="26"))
    assert label.read(workspace / "c.mp4").quality == "26"
    assert open(workspace / "c.mp4", "rb").read().count("ClipShrink 9.9".encode()) == 1


def test_a_comment_of_the_cameras_own_is_never_written_over(workspace):
    from clipshrink import label
    src = workspace / "own.mp4"
    ffmpeg("-i", _hevc(workspace / "a.mp4"), "-c", "copy", "-metadata", "comment=Holiday in Spain", src)
    out = workspace / "own_l.mp4"
    assert not label.write(src, out, _label())
    assert not out.exists()


def test_a_matroska_label_keeps_every_packet(workspace):
    from clipshrink import label
    src = workspace / "a.mkv"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:d=1", "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx265",
           "-preset", "ultrafast", "-c:a", "flac", src)
    out = workspace / "a_l.mkv"
    assert label.write(src, out, _label())
    assert label._packets(src) == label._packets(out)
    assert label.read(out).original == "GX010001.MP4"


# --- the conversion carries it
def test_every_conversion_is_labelled(get_clip, workspace):
    from clipshrink import config, label
    from clipshrink.engine import Converter, sha256_file
    src = shutil.copy(get_clip("gopro_like_1080p_telemetry"), workspace / "videos" / "GX010001.MP4")
    c = Converter(db_path=workspace / "work" / "s.db", work_dir=workspace / "work" / "t", encoder="x265")
    c.add_files([src])
    c.run()
    rec = c.record(src)
    assert rec.status == "verified"
    lab = label.read(rec.output)
    assert (lab.version, lab.codec, lab.encoder) == (config.VERSION, "h265", "x265")
    assert lab.target == (95.0, 90.0) and lab.original == "GX010001.MP4"
    assert (round(lab.vmaf_mean, 2), round(lab.vmaf_p1, 2)) == (round(rec.vmaf_mean, 2), round(rec.vmaf_p1, 2))
    assert lab.quality == f"{rec.quality:g}" and lab.original_bytes == rec.source_size
    assert abs(lab.saved_bytes - (rec.source_size - rec.output_size)) < 4096
    assert rec.output_sha256 == sha256_file(rec.output)


def test_a_conversion_that_was_renamed_is_still_found(workspace):
    from clipshrink import label
    from clipshrink.scan import scan_folder
    v = workspace / "videos"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:d=1", "-c:v", "libx264", v / "trip.mp4")
    assert label.write(_hevc(workspace / "x.mp4"), v / "renamed by me.mp4", _label(original="trip.mp4"))
    res = scan_folder(v)
    assert res.has_conversion == {str(v / "trip.mp4"): str(v / "renamed by me.mp4")}


# --- settings now and then
def test_what_differs_from_the_settings_now():
    from clipshrink.label import Made, describe, differs
    now = Made("h265", (95.0, 90.0), (3840, 2160))
    assert not differs(Made("h265", (95.0, 90.0), (3840, 2160)), now)
    assert not differs(Made("h265", None, (3840, 2160)), now), "an unknown target is not a difference"
    assert differs(Made("h265", (93.0, 88.0), (3840, 2160)), now)
    assert differs(Made("av1", (95.0, 90.0), (3840, 2160)), now)
    assert differs(Made("h265", (95.0, 90.0), (1920, 1080)), now)
    assert describe(Made("h265", (93.0, 88.0), (3840, 2160)), now) == "H.265, 93 / 88"
    assert describe(Made("h265", (95.0, 90.0), (1920, 1080)), now) == "H.265, 95 / 90, 1920×1080"


@pytest.fixture
def win(workspace, qapp_instance):
    from clipshrink.ui import MainWindow
    w = MainWindow(db_path=workspace / "work" / "s.db", work_dir=workspace / "work" / "t")
    yield w
    w.close()


def _pair(workspace, target):
    from clipshrink import label
    v = workspace / "videos"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:d=1", "-c:v", "libx264", v / "a.mp4")
    lab = _label(target_mean=target[0], target_p1=target[1], original="a.mp4")
    assert label.write(_hevc(workspace / "x.mp4"), v / "a_(h265).mp4", lab)
    return v


def _state(w, row=0):
    from clipshrink.ui import tr
    cols = [w.scan_table.horizontalHeaderItem(c).text() for c in range(w.scan_table.columnCount())]
    return w.scan_table.item(row, cols.index(tr("State"))).text()


def test_a_conversion_made_with_the_same_settings_is_skipped(win, workspace):
    from PySide6.QtCore import Qt
    from clipshrink.scan import scan_folder
    win._show_scan(scan_folder(_pair(workspace, (95.0, 90.0))))
    assert _state(win) == "Already has a conversion" and not win.redo
    assert win.scan_table.item(0, 0).checkState() == Qt.Unchecked


def test_a_conversion_made_with_other_settings_can_be_redone(win, workspace):
    from PySide6.QtCore import Qt
    from clipshrink.scan import scan_folder
    v = _pair(workspace, (93.0, 88.0))
    win._show_scan(scan_folder(v))
    assert _state(win).startswith("Made with other settings (H.265, 93 / 88)")
    assert "(H.265, 95 / 90)" in _state(win)
    assert win.scan_table.item(0, 0).checkState() == Qt.Unchecked, "a redo is only done when you tick it"
    assert win.redo == {str(v / "a.mp4"): str(v / "a_(h265).mp4")}


def test_a_lone_conversion_says_who_made_it(win, workspace):
    from clipshrink.scan import scan_folder
    from clipshrink import label
    v = workspace / "videos"
    assert label.write(_hevc(workspace / "x.mp4"), v / "holiday_(h265).mp4", _label(target_mean=93.0, target_p1=88.0))
    win._show_scan(scan_folder(v))
    assert _state(win) == "Already H.265, skipped (converted by ClipShrink 9.9 at 93 / 88)"


def test_a_redo_replaces_the_earlier_conversion(get_clip, workspace, monkeypatch):
    from clipshrink import config, label, trash
    from clipshrink.engine import Converter
    v = workspace / "videos"
    src = shutil.copy(get_clip("no_audio_no_telemetry"), v / "a.mp4")
    old = v / "a_(h265).mp4"
    assert label.write(_hevc(workspace / "x.mp4"), old, _label(target_mean=93.0, target_p1=88.0, original="a.mp4"))
    binned = workspace / "bin"
    binned.mkdir()
    monkeypatch.setattr(trash, "_send_to_recycle_bin", lambda p: shutil.move(p, binned / Path(p).name) and True)
    c = Converter(db_path=workspace / "work" / "s.db", work_dir=workspace / "work" / "t", encoder="x265")
    c.add_files([src], redo={str(src): str(old)})
    c.run()
    rec = c.record(src)
    assert rec.status == "verified" and Path(rec.output) == old
    assert label.read(old).target == (95.0, 90.0) and label.read(old).version == config.VERSION
    assert (binned / old.name).exists(), "the earlier conversion is in the Recycle Bin"
