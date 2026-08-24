"""命令行入口：``subtitles`` 启动 GUI。"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="subtitles",
        description="实时字幕：采集系统音频，ASR 识别后在悬浮窗显示双语字幕。",
    )
    parser.parse_args()

    from .gui.app import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
