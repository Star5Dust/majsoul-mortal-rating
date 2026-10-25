# -*- coding: utf-8 -*-
"""
探测 mjai 排队页 /progress?task= 怎么判断分析完成。只提交 1 局。

用法（在 查rt工具 目录里，rating_tool 环境）:
    python v6_probe\\probe_progress.py

结果写到 v6_probe\\progress_result.txt，排队页 HTML 存到 v6_probe\\progress_page.html。
"""

import importlib.util
import json
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("rt", HERE.parent / "查rt工具.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

# 心转手大铲车 第 10 局，46/53 步，局短分析快
PAIPU = "261021-1ee5ad22-5198-4cbd-b05d-c5b4fbdffbe7_a217758789"

JS_SUBMIT_FULL = rt.JS_SUBMIT.replace("(await r.text()).slice(0, 4000)", "(await r.text())")

JS_POLL = """
const [url, done] = arguments;
fetch(url, {credentials: 'same-origin', cache: 'no-store'})
  .then(async r => done({status: r.status, url: r.url, redirected: r.redirected, text: await r.text()}))
  .catch(e => done({status: 0, url: '', redirected: false, text: String(e)}));
"""

lines = []


def log(*parts):
    text = " ".join(str(p) for p in parts)
    print(text)
    lines.append(text)


def main():
    driver = rt.build_driver()
    try:
        mjai = rt.MjaiSession(driver, "4.1b", 0)
        mjai.open_form()
        t0 = time.time()
        mjai.last_token = mjai.wait_token()
        log(f"token {time.time() - t0:.1f}s")
        t1 = time.time()
        res = driver.execute_async_script(JS_SUBMIT_FULL, f"https://game.maj-soul.com/1/?paipu={PAIPU}", "4.1b", 180)
        log(f"submit {time.time() - t1:.1f}s status={res['status']} url={res['url']} len={len(res['text'])}")
        (HERE / "progress_page.html").write_text(res["text"], encoding="utf-8")
        html = res["text"]
        for pattern in (r"<meta[^>]+refresh[^>]*>", r"fetch\([^)]*\)", r"XMLHttpRequest", r"EventSource\([^)]*\)",
                        r"WebSocket\([^)]*\)", r"location[^;]{0,120}", r"setTimeout\([^)]{0,200}\)",
                        r"setInterval\([^)]{0,200}\)", r"/report/[^\"' ]+"):
            for m in re.findall(pattern, html)[:5]:
                log(f"  [{pattern}] {m[:200]}")
        progress_url = res["url"]
        if "/progress" not in progress_url:
            log("没有进排队页，直接拿到结果，结束")
            return
        log("\n--- 用页内 fetch 轮询排队页（不打开 KillerDucky）---")
        started = time.time()
        while time.time() - started < 120:
            r = driver.execute_async_script(JS_POLL, progress_url)
            found = rt.REPORT_RE.search(r["url"]) or rt.REPORT_RE.search(r["text"])
            log(f"t={time.time() - started:5.1f}s status={r['status']} redirected={r['redirected']} "
                f"url={r['url']} len={len(r['text'])} report={found.group(0) if found else None}")
            if found:
                break
            time.sleep(1)
        log("\n--- 再用标签页打开排队页，看它最终跳到哪里 ---")
        t2 = time.time()
        path = mjai.wait_progress(progress_url, timeout=120)
        log(f"tab 方式 {time.time() - t2:.1f}s -> {path}")
        report = rt.parse_report(mjai.fetch_report(path))
        log("report:", json.dumps(report, ensure_ascii=False))
    finally:
        (HERE / "progress_result.txt").write_text("\n".join(lines), encoding="utf-8")
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
