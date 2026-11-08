import os
import sys
from pathlib import Path
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_AUDIO_BACKEND", "pulseaudio")

import pytest

from delta_review.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data")


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    folder = tmp_path_factory.mktemp("media")
    target = folder / "synthetic-not-gameplay.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=2",
                    "-f", "lavfi", "-i", "color=c=white:s=320x180:r=30:d=2", "-f", "lavfi", "-i",
                    "aevalsrc=sin(440*2*PI*t)|sin(660*2*PI*t):s=48000:d=4",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(target)], check=True)
    return target
