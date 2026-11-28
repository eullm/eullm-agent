"""The editing of a video in scenes (Francesco, 2026-11-28: "dividere ...
decidere i secondi e quante scene", "scritte in sovraimpressione").

Each scene is a clip of the video model, with its own sound (the place, no
narrator), the narrator's words of the scene (a text-to-speech voice) and
a short text on screen. ffmpeg puts each scene together and joins them in
one vertical MP4. Everything happens in a temporary folder, nothing stays.

The text is drawn one line at a time, each centered, with a dark box under
it so it can be read on any image; the font is DejaVu Sans Bold (the
package fonts-dejavu-core of the image). Without the font the scenes have
no text, without ffmpeg there is no editing at all (videos.make then makes
one clip as before).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")
LINE = 16  # characters a line of the text on screen
MAX_TEMPO = 1.5  # a voice longer than its scene is spoken faster, up to this
BED = 0.22  # the volume of the clip's own sound under the voice
TIMEOUT = 300


class MontageError(RuntimeError):
    """ffmpeg could not put the video together."""


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def font() -> str | None:
    return next((f for f in FONTS if os.path.exists(f)), None)


def _run(args: list[str]) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MontageError(str(e)) from e
    if r.returncode != 0:
        raise MontageError((r.stderr or "")[-600:])
    return r.stdout


def probe(path: Path) -> dict:
    """width, height, duration and whether the file has sound."""
    out = json.loads(_run(["ffprobe", "-v", "error", "-show_entries",
                           "stream=codec_type,width,height:format=duration", "-of", "json", str(path)]) or "{}")
    streams = out.get("streams") or []
    v = next((x for x in streams if x.get("codec_type") == "video"), {})
    try:
        duration = float((out.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"width": int(v.get("width") or 0), "height": int(v.get("height") or 0), "duration": duration,
            "audio": any(x.get("codec_type") == "audio" for x in streams)}


def lines(text: str) -> list[str]:
    return textwrap.wrap(" ".join((text or "").split()), LINE)[:3]


def _even(x: int) -> int:
    return max(2, x - x % 2)


def segment(work: Path, i: int, clip: Path, voice: Path | None, text: str, seconds: float,
            size: tuple[int, int]) -> Path:
    """One scene: the clip fitted to the size and the seconds, the text on
    screen, the voice over the clip's own sound."""
    w, h = size
    info = probe(clip)
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(clip)]
    sound = "0:a"
    n = 1
    if not info["audio"]:
        args += ["-f", "lavfi", "-t", f"{seconds:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
        sound, n = "1:a", 2
    if voice is not None:
        args += ["-i", str(voice)]
    vf = [f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}", "fps=30",
          f"tpad=stop_mode=clone:stop_duration={seconds:.2f}", "setsar=1"]
    f = font()
    rows = lines(text) if f else []
    size_px = max(18, w // 15)
    for k, row in enumerate(rows):
        tf = work / f"t{i}-{k}.txt"
        tf.write_text(row, encoding="utf-8")
        y = f"h*0.16+{k}*{int(size_px * 1.45)}"
        vf.append(f"drawtext=fontfile={f}:textfile={tf}:fontcolor=white:fontsize={size_px}:box=1:"
                  f"boxcolor=black@0.55:boxborderw={size_px // 3}:x=(w-text_w)/2:y={y}")
    vf.append("format=yuv420p[v]")
    graph = [",".join(vf)]
    if voice is not None:
        vd = probe(voice)["duration"] or seconds
        tempo = min(MAX_TEMPO, max(1.0, vd / max(seconds - 0.3, 1.0)))
        graph.append(f"[{sound}]aresample=44100,volume={BED},apad[a0]")
        graph.append(f"[{n}:a]aresample=44100,atempo={tempo:.3f},apad[a1]")
        graph.append("[a0][a1]amix=inputs=2:duration=longest:normalize=0[a]")
    else:
        graph.append(f"[{sound}]aresample=44100,apad[a]")
    out = work / f"s{i}.mp4"
    _run(args + ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-t", f"{seconds:.2f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
                 "-ar", "44100", "-ac", "2", str(out)])
    return out


def assemble(scenes: list[dict], seconds: float) -> bytes:
    """The video of the scenes [{clip: bytes, voice: bytes | None, text}],
    each `seconds` long, as one MP4."""
    if not scenes:
        raise MontageError("no scenes")
    with tempfile.TemporaryDirectory(prefix="redazia-video-") as tmp:
        work = Path(tmp)
        clips = []
        for i, sc in enumerate(scenes):
            clip = work / f"c{i}.mp4"
            clip.write_bytes(sc["clip"])
            voice = None
            if sc.get("voice"):
                voice = work / f"v{i}.mp3"
                voice.write_bytes(sc["voice"])
            clips.append((clip, voice, sc.get("text") or ""))
        first = probe(clips[0][0])
        if not first["width"] or not first["height"]:
            raise MontageError("the first clip has no video")
        size = (_even(first["width"]), _even(first["height"]))
        parts = [segment(work, i, c, v, t, seconds, size) for i, (c, v, t) in enumerate(clips)]
        listing = work / "list.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        out = work / "video.mp4"
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
              "-movflags", "+faststart", str(out)])
        return out.read_bytes()
