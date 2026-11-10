"""A plain CSV report for every batch, kept in the "reports" folder for the user's
information. The app writes them and never reads them back."""
import csv, datetime, os, platform
from pathlib import Path
from . import config
from .i18n import N_, reason, tr

COLUMNS = [N_(c) for c in ("Started", "Finished", "Time taken", "File", "Folder", "Result", "Reason", "Before (bytes)", "After (bytes)", "Saved %",
           "VMAF average", "VMAF worst 1%", "Encoder", "Quality setting", "Conversion", "Codec", "Output resolution",
           "Output bitrate (Mbit/s)", "Target average", "Target worst 1%")]
CONVERTED = ("verified", "below_bar", "original_deleted")


SUMMARY = [N_(c) for c in ("Batch started", "Last file finished", "Total time", "Encoder", "Graphics card",
                            "Processor")]


def reports_dir(db_path):
    return Path(db_path).parent / "reports"


def _safe(text):
    """Stops a file name acting as a formula when the report opens in a spreadsheet."""
    text = str(text or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def regional_format():
    """(separator, decimal symbol) that Excel on this PC expects when a CSV is double-clicked.
    Windows set to French, Dutch, German... uses ";" and "," so a plain comma file falls apart."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as k:
            sep = winreg.QueryValueEx(k, "sList")[0]
            dec = winreg.QueryValueEx(k, "sDecimal")[0]
    except (ImportError, OSError):
        return ",", "."
    sep = sep if sep in (",", ";", "\t", "|") else ","
    dec = dec if len(dec) == 1 and dec != sep else "."
    return sep, dec


def _when(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else ""


def _took(seconds):
    if seconds is None or seconds < 0:
        return ""
    s = int(round(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _row(r):
    converted = r.status in CONVERTED and r.output_size
    finished = r.finished or (r.updated if not r.started else 0)
    return {
        "Started": _when(r.started),
        "Finished": _when(finished),
        "Time taken": _took(finished - r.started) if r.started and finished else "",
        "File": _safe(Path(r.source).name),
        "Folder": _safe(Path(r.source).parent),
        "Result": config.status_text(r),
        "Reason": _safe(reason(r.reason)),
        "Before (bytes)": r.source_size,
        "After (bytes)": r.output_size if converted else "",
        "Saved %": round((1 - r.output_size / r.source_size) * 100, 1) if converted and r.source_size else "",
        "VMAF average": round(r.vmaf_mean, 2) if r.vmaf_mean is not None else "",
        "VMAF worst 1%": round(r.vmaf_p1, 2) if r.vmaf_p1 is not None else "",
        "Encoder": r.encoder,
        "Quality setting": r.quality if r.quality is not None else "",
        "Conversion": _safe(r.output) if converted else "",
        "Codec": config.CODEC_NAMES.get(r.codec, "") if converted else "",
        "Output resolution": f"{r.output_width}x{r.output_height}" if converted and r.output_width else "",
        "Output bitrate (Mbit/s)": round(r.output_bitrate_mbit, 2) if converted and r.output_bitrate_mbit else "",
        "Target average": r.warn_mean if converted else "",
        "Target worst 1%": r.warn_p1 if converted else "",
    }


def hardware():
    """What the batch ran on, for the report. Names only, nothing that identifies the PC."""
    from . import encode
    cpu = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            cpu = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except (ImportError, OSError):
        cpu = platform.processor()
    return {"Graphics card": encode.gpu_name() or tr("None found"), "Processor": cpu or "?",
            "Windows": f"{platform.release()} ({platform.version()})"}


def _summary(records, batch):
    started = batch.get("started") or min((r.started for r in records if r.started), default=0)
    last = max((r.finished or r.updated or 0 for r in records), default=0)
    enc = batch.get("encoder") or next((r.encoder for r in records if r.encoder), "")
    rows = {"Batch started": _when(started), "Last file finished": _when(last),
            "Total time": _took(last - started) if started and last else "",
            "Encoder": f"{enc} ({tr(config.ENCODER_NAMES[enc])})" if enc in config.ENCODER_NAMES else enc}
    rows.update(batch.get("hardware") or hardware())
    return [[tr(k), rows.get(k, "")] for k in SUMMARY] + [["Windows", rows.get("Windows", "")],
                                                         ["ClipShrink", config.VERSION]]


def write_batch_report(folder, records, path=None, batch=None):
    """Writes or rewrites one batch's report. Rewriting after each file means a
    crash keeps the report of everything already finished. Under the table, after
    an empty line, comes the batch's total time and the hardware it ran on."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if path is None:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path, n = folder / f"report_{stamp}.csv", 2
        while path.exists():
            path, n = folder / f"report_{stamp}_{n}.csv", n + 1
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        # Written in the app's language at the time, with this PC's separator and decimal
        # symbol so Excel opens it in columns. Numbers carry no thousands separator.
        sep, dec = regional_format()
        w = csv.writer(f, delimiter=sep)

        def cell(v):
            return str(v).replace(".", dec) if isinstance(v, float) else v
        w.writerow([tr(c) for c in COLUMNS])
        w.writerows([cell(row[c]) for c in COLUMNS] for row in map(_row, records))
        w.writerow([])
        w.writerows(_summary(records, batch or {}))
    os.replace(tmp, path)
    return Path(path)
