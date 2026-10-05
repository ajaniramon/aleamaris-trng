import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "src", "bin", "trng_cli.py")
VIDEO = os.path.join(ROOT, "sample.MP4")


def run(*args):
    env = dict(os.environ, ALEAMARIS_LOG_LEVEL="ERROR")
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, env=env, timeout=120)


def test_cli_non_multiple_of_block_size(tmp_path):
    out = tmp_path / "o.bin"
    r = run("--video", VIDEO, "--demo", "--bytes", "100", "--out", str(out))
    assert r.returncode == 0, r.stderr.decode()
    assert out.stat().st_size == 100


def test_cli_recording_without_demo_produces_nothing(tmp_path):
    out = tmp_path / "o.bin"
    r = run("--video", VIDEO, "--bytes", "32", "--max-frames", "5", "--out", str(out))
    assert r.returncode == 1
    assert out.stat().st_size == 0
