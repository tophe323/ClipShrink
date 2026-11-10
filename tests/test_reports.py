"""Reports are kept automatically for every batch, for the user's information.
The app writes them and never reads them back."""
import csv
from pathlib import Path
from conftest import copy_preserving
from clipshrink import db as database


def rows_of(path):
    from conftest import report_rows
    return report_rows(path)


def reports_in(db):
    from clipshrink.reports import reports_dir
    folder = reports_dir(db)
    return sorted(folder.glob("*.csv")) if folder.exists() else []


def test_batch_writes_a_report_automatically(get_clip, workspace):
    from clipshrink.engine import Converter
    src = copy_preserving(get_clip("no_audio_no_telemetry"), workspace / "videos" / "a.mp4")
    db = workspace / "work" / "s.db"
    c = Converter(db_path=db, work_dir=workspace / "work" / "t", encoder="x265")
    c.add_files([src])
    c.run()
    found = reports_in(db)
    assert len(found) == 1
    row = rows_of(found[0])[0]
    assert row["File"] == "a.mp4" and Path(row["Folder"]) == src.parent
    assert float(row["VMAF average"]) >= 95 and row["Result"] == "Verified"
    assert int(row["After (bytes)"]) == Path(c.record(src).output).stat().st_size


def test_each_batch_gets_its_own_report_and_old_ones_stay(get_clip, workspace):
    from clipshrink.engine import Converter
    db = workspace / "work" / "s.db"
    for n in ("a.mp4", "b.mp4"):
        src = copy_preserving(get_clip("no_audio_no_telemetry"), workspace / "videos" / n)
        c = Converter(db_path=db, work_dir=workspace / "work" / "t", encoder="x265")
        c.add_files([src])
        c.run()
    found = reports_in(db)
    assert len(found) == 2 and {rows_of(r)[0]["File"] for r in found} == {"a.mp4", "b.mp4"}


def test_batch_with_nothing_to_do_writes_no_report(get_clip, workspace):
    from clipshrink.engine import Converter
    src = copy_preserving(get_clip("no_audio_no_telemetry"), workspace / "videos" / "a.mp4")
    db = workspace / "work" / "s.db"
    for _ in range(2):
        c = Converter(db_path=db, work_dir=workspace / "work" / "t", encoder="x265")
        c.add_files([src])
        c.run()
    assert len(reports_in(db)) == 1


def test_forgetting_files_keeps_the_reports(get_clip, workspace):
    from clipshrink.engine import Converter, forget_everything
    src = copy_preserving(get_clip("no_audio_no_telemetry"), workspace / "videos" / "a.mp4")
    db = workspace / "work" / "s.db"
    c = Converter(db_path=db, work_dir=workspace / "work" / "t", encoder="x265")
    c.add_files([src])
    c.run()
    forget_everything(db, workspace / "work" / "t")
    assert database.read_records(db) == [] and len(reports_in(db)) == 1


def test_hostile_file_name_cannot_become_a_spreadsheet_formula(workspace):
    from clipshrink.reports import write_batch_report
    rec = database.Record(source="C:/Videos/=cmd.mp4", status="verified", source_size=10, output_size=5)
    rep = write_batch_report(workspace / "r", [rec])
    assert rows_of(rep)[0]["File"].startswith("'=")


def test_reports_never_overwrite_each_other(workspace):
    from clipshrink.reports import write_batch_report
    rec = database.Record(source="C:/Videos/a.mp4", status="verified", source_size=10, output_size=5)
    a = write_batch_report(workspace / "r", [rec])
    b = write_batch_report(workspace / "r", [rec])
    assert a != b and a.exists() and b.exists()


# --- 1.7.1: times, batch summary, hardware, and the PC's own CSV format

def _summary(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        tail = f.read().split("\r\n\r\n")[1]
    return dict(r[:2] for r in csv.reader(tail.splitlines()))


def test_each_file_has_its_start_finish_and_time_taken(get_clip, workspace):
    from datetime import datetime
    from clipshrink.engine import Converter
    src = copy_preserving(get_clip("no_audio_no_telemetry"), workspace / "videos" / "a.mp4")
    db = workspace / "work" / "s.db"
    c = Converter(db_path=db, work_dir=workspace / "work" / "t", encoder="x265")
    c.add_files([src])
    c.run()
    rec = c.record(src)
    assert 0 < rec.started <= rec.finished
    row = rows_of(reports_in(db)[0])[0]
    a = datetime.strptime(row["Started"], "%Y-%m-%d %H:%M:%S")
    b = datetime.strptime(row["Finished"], "%Y-%m-%d %H:%M:%S")
    h, m, s = map(int, row["Time taken"].split(":"))
    assert a <= b and abs((b - a).total_seconds() - (h * 3600 + m * 60 + s)) <= 1


def test_the_report_ends_with_the_batch_time_and_hardware(get_clip, workspace):
    from clipshrink import config
    from clipshrink.engine import Converter
    db = workspace / "work" / "s.db"
    srcs = [copy_preserving(get_clip("no_audio_no_telemetry"), workspace / "videos" / n) for n in ("a.mp4", "b.mp4")]
    c = Converter(db_path=db, work_dir=workspace / "work" / "t", encoder="x265")
    c.add_files(srcs)
    c.run()
    rep = reports_in(db)[0]
    assert len(rows_of(rep)) == 2
    s = _summary(rep)
    assert s["Batch started"] and s["Last file finished"] >= s["Batch started"]
    h, m, sec = map(int, s["Total time"].split(":"))
    assert h * 3600 + m * 60 + sec >= 1
    assert s["Encoder"] == "x265 (processor)"
    assert s["Processor"] and s["Graphics card"] and s["Windows"]
    assert s["ClipShrink"] == config.VERSION


def test_report_uses_the_separator_and_decimal_excel_expects_here(workspace, monkeypatch):
    """Windows in French, Dutch or German uses ";" and ","; Excel splits on those."""
    from clipshrink import reports
    monkeypatch.setattr(reports, "regional_format", lambda: (";", ","))
    recs = [database.Record(source="C:/Videos/a, b; c.mp4", status="verified", source_size=1000, output_size=400,
                            vmaf_mean=97.25, vmaf_p1=91.5, duration=10.0),
            database.Record(source="C:/Videos/d.mp4", status="kept_original", source_size=10,
                            reason="Conversion did not match the original, original kept: duration differs "
                                   "(9.600s vs 9.700s); frame count differs (1 vs 2)")]
    rep = reports.write_batch_report(workspace / "r", recs, batch={"hardware": {}})
    with open(rep, newline="", encoding="utf-8-sig") as f:
        table = f.read().split("\r\n\r\n")[0]
    rows = list(csv.reader(table.splitlines(), delimiter=";"))
    head = rows[0]
    assert all(len(r) == len(head) for r in rows), "every row keeps its columns"
    one = dict(zip(head, rows[1]))
    assert one["File"] == "a, b; c.mp4" and one["VMAF average"] == "97,25" and one["Saved %"] == "60,0"
    assert dict(zip(head, rows[2]))["Reason"].endswith("frame count differs (1 vs 2)")


def test_regional_format_reads_windows_and_never_clashes(monkeypatch):
    from clipshrink import reports
    monkeypatch.undo()      # the tests' plain format, set in conftest
    real = reports.regional_format()
    assert real[0] in (",", ";", "\t", "|") and len(real[1]) == 1 and real[0] != real[1]
