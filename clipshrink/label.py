"""A short note inside every conversion: which version of this tool made it, with what settings,
how it scored and how much it saved. It travels with the file, so a scan on any PC can tell a
conversion apart and see whether it was made with the settings in use now.

MP4 and MOV get it as a comment (udta/meta/ilst ©cmt), which Windows shows for MP4 under
Properties > Details > Comments, as do VLC and other players. Matroska gets a CLIPSHRINK tag. The note is written after every check has passed, and the result is proved to hold
exactly the same picture, sound and telemetry as the checked file before it is delivered.
"""
import os, re, struct, subprocess
from dataclasses import dataclass
from pathlib import Path
from . import mp4graft, tools

PREFIX = "ClipShrink "
MKV_TAG = "CLIPSHRINK"
_CMT = b"\xa9cmt"


@dataclass
class Label:
    version: str
    codec: str = ""
    encoder: str = ""
    quality: str = ""
    target_mean: float = None
    target_p1: float = None
    vmaf_mean: float = None
    vmaf_p1: float = None
    original_bytes: int = 0
    saved_bytes: int = 0
    date: str = ""
    original: str = ""

    @property
    def target(self):
        return (self.target_mean, self.target_p1) if self.target_mean is not None else None


def _num(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


def make(lab):
    """The text written into the file. The original's name comes last, as it may hold anything."""
    parts = [PREFIX + lab.version, f"codec={lab.codec}", f"encoder={lab.encoder}", f"quality={_num(lab.quality)}",
             f"target={_num(lab.target_mean)}/{_num(lab.target_p1)}",
             f"vmaf={lab.vmaf_mean:.2f}/{lab.vmaf_p1:.2f}", f"original_bytes={lab.original_bytes}",
             f"saved_bytes={lab.saved_bytes}", f"date={lab.date}", f"original={lab.original}"]
    return "; ".join(parts)


def parse(text):
    """The label in `text`, or None if it isn't one of ours."""
    if not text or not text.startswith(PREFIX):
        return None
    head, _, rest = text.partition("; ")
    version = head[len(PREFIX):].strip()
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        return None
    lab = Label(version=version)
    original = re.search(r"(?:^|; )original=(.*)$", rest)
    if original:
        lab.original = original.group(1)
        rest = rest[:original.start()]
    for part in rest.split("; "):
        key, _, value = part.partition("=")
        try:
            if key in ("codec", "encoder", "quality", "date"):
                setattr(lab, key, value)
            elif key in ("target", "vmaf"):
                a, b = (float(x) for x in value.split("/"))
                setattr(lab, key + "_mean", a)
                setattr(lab, key + "_p1", b)
            elif key in ("original_bytes", "saved_bytes"):
                setattr(lab, key, int(value))
        except ValueError:
            continue
    return lab


def from_tags(tags):
    """The label among a file's tags as ffprobe reports them, or None."""
    for key, value in (tags or {}).items():
        if key.lower() in ("comment", MKV_TAG.lower()):
            lab = parse(value)
            if lab:
                return lab
    return None


# --- comparing with the settings in use now -------------------------------------

@dataclass(frozen=True)
class Made:
    """How a conversion was made, or would be made now. None where it isn't known."""
    codec: str = None           # h265 or av1
    target: tuple = None        # (average, worst 1%) it was judged by
    size: tuple = None          # (width, height) of the picture


_CODECS = {"hevc": "h265", "av1": "av1"}


def previous_settings(original, conversion=None, rec=None):
    """How an existing conversion was made: from its label, its own picture, and this PC's memory."""
    codec = _CODECS.get(conversion.codec) if conversion else (rec.codec or None if rec else None)
    lab = conversion.label if conversion else None
    target = lab.target if lab and lab.target else ((rec.warn_mean, rec.warn_p1) if rec else None)
    if conversion:
        size = (conversion.width, conversion.height)
    else:
        size = (rec.output_width, rec.output_height) if rec and rec.output_width else None
    return Made(codec, target, size)


def current_settings(original, settings):
    """How the original would be converted with `settings` (settings.Settings)."""
    from . import config
    p = settings.policy()
    size = config.target_size(original.width, original.height, settings.max_resolution)
    return Made(settings.codec, (p.warn_mean, p.warn_p1), size or (original.width, original.height))


def differs(before, now):
    """True when something known about the earlier conversion isn't what the settings now ask for."""
    return any(a is not None and b is not None and a != b for a, b in
               ((before.codec, now.codec), (before.target, now.target), (before.size, now.size)))


def describe(m, other=None):
    """For example "H.265, 95 / 90", with the picture size when it is what differs from `other`."""
    from . import config
    from .i18n import g
    parts = [config.CODEC_NAMES.get(m.codec, m.codec)] if m.codec else []
    if m.target:
        parts.append(f"{g(m.target[0])} / {g(m.target[1])}")
    if m.size and (other is None or (other.size and other.size != m.size)):
        parts.append(f"{m.size[0]}×{m.size[1]}")
    return ", ".join(parts)


# --- MP4 and MOV -------------------------------------------------------------

def _top_boxes(path):
    size = os.path.getsize(path)
    out, pos = [], 0
    with open(path, "rb") as f:
        while pos + 8 <= size:
            f.seek(pos)
            s, k = struct.unpack(">I4s", f.read(8))
            if s == 1:
                s = struct.unpack(">Q", f.read(8))[0]
            elif s == 0:
                s = size - pos
            if s < 8 or pos + s > size:
                raise ValueError("damaged MP4 file")
            out.append((k, pos, pos + s))
            pos += s
    return out


_box = mp4graft._box
# The iTunes-style list (udta/meta/ilst) is what Windows reads for Properties > Details > Comments.
_HDLR = _box(b"hdlr", b"\0" * 8 + b"mdirappl" + b"\0" * 9)


def _item(text):
    return _box(_CMT, _box(b"data", struct.pack(">II", 1, 0) + text.encode("utf-8")))


def _item_text(payload):
    d = mp4graft._find(payload, 0, len(payload), b"data")
    return payload[d[1] + 8:d[2]].decode("utf-8", "replace") if d else ""


def _with_item(ilst, text):
    """The list's payload with our comment in it, replacing an earlier one of ours. None if the
    file has a comment of its own, which is never written over."""
    out = bytearray()
    for kind, b, s, e in mp4graft._boxes(ilst, 0, len(ilst)):
        if kind == _CMT:
            if not _item_text(ilst[s:e]).startswith(PREFIX):
                return None
            continue
        out += ilst[b:e]
    return bytes(out) + _item(text)


def _with_meta(meta, text):
    """The meta box's payload with the comment added, or None if it isn't an iTunes-style list."""
    head = 0 if meta[4:8] == b"hdlr" else 4     # QuickTime leaves out the version field
    hd = mp4graft._find(meta, head, len(meta), b"hdlr")
    if not hd or meta[hd[1] + 8:hd[1] + 12] != b"mdir":
        return None
    out, done = bytearray(meta[:head]), False
    for kind, b, s, e in mp4graft._boxes(meta, head, len(meta)):
        if kind == b"ilst":
            items = _with_item(meta[s:e], text)
            if items is None:
                return None
            out += _box(b"ilst", items)
            done = True
        else:
            out += meta[b:e]
    if not done:
        out += _box(b"ilst", _item(text))
    return bytes(out)


def _with_label(udta, text):
    """The camera info's payload (udta) with the label in it, or None if it can't carry one.
    Everything else in it stays byte for byte."""
    out, done, end = bytearray(), False, 0
    for kind, b, s, e in mp4graft._boxes(udta, 0, len(udta)):
        end = e
        if kind == b"meta" and not done:
            meta = _with_meta(udta[s:e], text)
            if meta is None:
                return None
            out += _box(b"meta", meta)
            done = True
        else:
            out += udta[b:e]
    if not done:
        out += _box(b"meta", b"\0\0\0\0" + _HDLR + _box(b"ilst", _item(text)))
    return bytes(out) + udta[end:]


def without_label(udta):
    """The camera info's payload (udta) with our label taken out, so it can be compared byte for
    byte with the original's. The exact inverse of _with_label."""
    out, end = bytearray(), 0
    ours = b"\0\0\0\0" + _HDLR
    for kind, b, s, e in mp4graft._boxes(udta, 0, len(udta)):
        end = e
        if kind != b"meta":
            out += udta[b:e]
            continue
        meta = udta[s:e]
        head = 0 if meta[4:8] == b"hdlr" else 4
        rebuilt, items = bytearray(meta[:head]), None
        for k2, b2, s2, e2 in mp4graft._boxes(meta, head, len(meta)):
            if k2 == b"ilst":
                items = bytearray()
                for k3, b3, s3, e3 in mp4graft._boxes(meta, s2, e2):
                    if not (k3 == _CMT and _item_text(meta[s3:e3]).startswith(PREFIX)):
                        items += meta[b3:e3]
                rebuilt += _box(b"ilst", bytes(items))
            else:
                rebuilt += meta[b2:e2]
        if items is not None and not items and bytes(rebuilt) == ours + _box(b"ilst", b""):
            continue        # the whole box was ours
        out += _box(b"meta", bytes(rebuilt))
    return bytes(out) + udta[end:]


def _shift(trak, delta, after):
    """The track with every chunk offset at or past `after` moved by `delta`. None if it can't be done."""
    for kind, fmt, width in ((b"stco", "I", 4), (b"co64", "Q", 8)):
        co = mp4graft._find(trak, 8, len(trak), *mp4graft.STBL, kind)
        if co:
            n = struct.unpack_from(">I", trak, co[1] + 4)[0]
            offs = struct.unpack_from(f">{n}{fmt}", trak, co[1] + 8)
            moved = [o + delta if o >= after else o for o in offs]
            if width == 4 and moved and max(moved) >= 1 << 32:
                return None
            new = mp4graft._box(kind, struct.pack(">II", 0, n) + struct.pack(f">{n}{fmt}", *moved))
            return mp4graft._replace_child(trak, mp4graft.STBL + (kind,), new)
    return trak


def _labelled_moov(moov, text, moov_end):
    """The new index with the label in its camera info, its size change, or (None, 0) if it can't carry one."""
    body = bytearray()
    found = False
    for kind, b, s, e in mp4graft._boxes(moov, 8, len(moov)):
        if kind == b"udta" and not found:
            udta = _with_label(moov[s:e], text)
            if udta is None:
                return None, 0
            body += _box(b"udta", udta)
            found = True
        else:
            body += moov[b:e]
    if not found:
        body += _box(b"udta", _with_label(b"", text))
    delta = len(body) + 8 - len(moov)
    if not delta:
        return mp4graft._box(b"moov", bytes(body)), 0
    shifted = bytearray()
    for kind, b, s, e in mp4graft._boxes(bytes(body), 0, len(body)):
        if kind == b"trak":
            t = _shift(bytes(body[b:e]), delta, moov_end)
            if t is None:
                return None, 0
            shifted += t
        else:
            shifted += body[b:e]
    if len(shifted) != len(body):
        return None, 0
    return mp4graft._box(b"moov", bytes(shifted)), delta


def _write_mp4(src, dst, text, checkpoint=None):
    boxes = _top_boxes(src)
    moovs = [(b, e) for k, b, e in boxes if k == b"moov"]
    if len(moovs) != 1 or any(k == b"moof" for k, _, _ in boxes):
        return False
    mb, me = moovs[0]
    with open(src, "rb") as f:
        f.seek(mb)
        moov = f.read(me - mb)
    if struct.unpack_from(">I", moov, 0)[0] == 1:
        return False        # a 64-bit index header; never seen from our own output
    new, _ = _labelled_moov(moov, text, me)
    if new is None:
        return False
    with open(src, "rb") as a, open(dst, "wb") as b:
        _copy(a, b, 0, mb, checkpoint)
        b.write(new)
        _copy(a, b, me, os.path.getsize(src), checkpoint)
    return True


def _copy(a, b, start, end, checkpoint):
    a.seek(start)
    left = end - start
    while left:
        data = a.read(min(left, 4 << 20))
        if not data:
            raise ValueError("file is shorter than expected")
        b.write(data)
        left -= len(data)
        if checkpoint:
            checkpoint()


def _same_mp4(checked, labelled, text):
    """Every track's samples, in order, are what was checked, and the camera info is the checked
    one with only the label added."""
    if mp4graft.nonvideo_digests(checked, skip_video=False) != mp4graft.nonvideo_digests(labelled, skip_video=False):
        return False
    a, b = mp4graft.read_moov(checked), mp4graft.read_moov(labelled)
    if [t.sizes for t in mp4graft.parse_tracks(a)] != [t.sizes for t in mp4graft.parse_tracks(b)]:
        return False
    return _with_label(mp4graft.udta(checked), text) == mp4graft.udta(labelled)


# --- Matroska ------------------------------------------------------------------

def _packets(path):
    """Every packet's stream, times, size and checksum, as FFmpeg reads them."""
    r = tools.run([tools.ffmpeg(), "-v", "error", "-i", str(path), "-map", "0", "-c", "copy", "-f", "framemd5", "-"])
    if r.returncode:
        raise ValueError(r.stderr.strip()[-300:])
    return [line for line in r.stdout.splitlines() if line and (not line.startswith("#") or line.startswith("#tb"))]


def _write_mkv(src, dst, text, checkpoint=None):
    r = tools.run([tools.ffmpeg(), "-v", "error", "-y", "-i", str(src), "-map", "0", "-c", "copy", "-map_metadata", "0",
                   "-metadata", f"{MKV_TAG}={text}", "-f", "matroska", str(dst)])
    return r.returncode == 0


# --- both -----------------------------------------------------------------------

def write(checked, dst, text, checkpoint=None):
    """Writes `checked` to `dst` with the label. True if it was labelled and proved identical in
    content; False leaves `dst` absent, and the caller delivers the file without a label."""
    checked, dst = Path(checked), Path(dst)
    try:
        if checked.suffix.lower() == ".mkv":
            ok = _write_mkv(checked, dst, text, checkpoint) and _packets(checked) == _packets(dst)
        else:
            ok = _write_mp4(checked, dst, text, checkpoint) and _same_mp4(checked, dst, text)
    except (OSError, ValueError, struct.error, subprocess.SubprocessError):
        ok = False
    if not ok:
        dst.unlink(missing_ok=True)
    return ok


def read(path):
    """The label in a file, or None. Reads the tags with ffprobe, so it works for every format."""
    r = tools.run([tools.ffprobe(), "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)])
    if r.returncode:
        return None
    import json
    try:
        return from_tags(json.loads(r.stdout).get("format", {}).get("tags"))
    except ValueError:
        return None
