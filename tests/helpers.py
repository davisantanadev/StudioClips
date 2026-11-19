import subprocess
from pathlib import Path

from clipador.recorder import ffmpeg_bin


def fake_live_cmd(seconds: int) -> list[str]:
    """Fonte fake que imita streamlink --stdout: MPEG-TS 1280x720 com áudio, em tempo real."""
    return [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-re",
            "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "aac",
            "-f", "mpegts", "pipe:1"]


def make_video(path: Path, seconds: float, audio: bool = True) -> Path:
    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())
