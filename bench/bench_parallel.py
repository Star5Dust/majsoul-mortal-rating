# -*- coding: utf-8 -*-
"""
测不同并行数的速度（会用 --refresh 重新提交，每档 GAMES 局）。

用法（在 查rt工具 目录里，rating_tool 环境）:
    python bench\\bench_parallel.py
结果追加到 bench\\bench_result.txt
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "bench_result.txt"
ARGS = ["--nickname", "心转手大铲车", "--player-id", "23260736", "--room", "12", "--games", "8", "--refresh"]
LEVELS = [1, 2, 3, 4]
COOLDOWN = 90


def main():
    summary = []
    for i, k in enumerate(LEVELS):
        if i:
            print(f"\n=== 冷却 {COOLDOWN}s ===")
            time.sleep(COOLDOWN)
        print(f"\n=== 并行 {k} ===")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "查rt工具.py"), *ARGS, "--parallel", str(k)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        chunks = []
        for line in proc.stdout:
            print(line, end="", flush=True)
            chunks.append(line)
        proc.wait()
        text = "".join(chunks)
        with OUT.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 并行 {k} =====\n{text}\n")
        mjai = re.search(r"\[time\] mjai (\d+) 局 ([\d.]+)s（平均 ([\d.]+)s/局）", text)
        total = re.search(r"\[done\] 用时 (\d+)s", text)
        limited = re.search(r"限流 (\d+) 次，最后并行 (\d+)", text)
        ok = re.search(r"成功 (\d+)/(\d+)", text)
        summary.append((k, mjai.group(2) if mjai else "?", mjai.group(3) if mjai else "?",
                        total.group(1) if total else "?", limited.group(1) if limited else "?",
                        limited.group(2) if limited else "?", f"{ok.group(1)}/{ok.group(2)}" if ok else "?"))
        if limited and int(limited.group(1)) >= 2:
            print("限流 2 次以上，停止加大并行")
            break
    lines = ["并行 | mjai 用时 | 平均每局 | 总用时 | 限流次数 | 最后并行 | 成功"]
    lines += [" | ".join(str(x) for x in row) for row in summary]
    report = "\n".join(lines)
    print("\n" + report)
    with OUT.open("a", encoding="utf-8") as f:
        f.write("\n" + report + "\n")


if __name__ == "__main__":
    main()
