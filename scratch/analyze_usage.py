#!/usr/bin/env python3
import argparse
import re
import subprocess


def parse_logs(since=None, until=None):
    cmd = ["journalctl", "--user", "-u", "aistudio-bridge", "--no-pager"]
    if since:
        cmd.extend(["--since", since])
    if until:
        cmd.extend(["--until", until])

    try:
        lines = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines()
    except Exception as e:
        print(f"Error: {e}")
        return []

    # Happy path regex: [DONE] ... Token Usage: Prompt=X Cached=Y Output=Z Thoughts=W Total=T
    pattern = re.compile(
        r"\[DONE\]\s+(?:GET|POST)\s+https://.*?/models/([^?:\s]+)(?::([^?:\s]+))?.*?"
        r"Token Usage: Prompt=([0-9,]+) Cached=([0-9,]+) Output=([0-9,]+) Thoughts=([0-9,]+) Total=([0-9,]+)"
    )

    data = []
    for line in lines:
        match = pattern.search(line)
        if match:
            model, method, p, c, o, th, t = match.groups()
            data.append(
                {
                    "model": model,
                    "method": method or "unknown",
                    "prompt": int(p.replace(",", "")),
                    "cached": int(c.replace(",", "")),
                    "output": int(o.replace(",", "")),
                    "thoughts": int(th.replace(",", "")),
                    "total": int(t.replace(",", "")),
                }
            )
    return data


def main():
    parser = argparse.ArgumentParser(description="Analyze aistudio-bridge token usage (Happy Path)")
    parser.add_argument("--since", default="today")
    parser.add_argument("--until")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    results = parse_logs(since=args.since, until=args.until)
    if not results:
        print("No usage data found for the current format.")
        return

    if args.all:
        print(f"{'Model':<30} | {'Method':<25} | {'Prompt':>12} | {'Cached':>12} | {'Output':>12} | {'Total':>12}")
        print("-" * 125)
        for r in results:
            print(
                f"{r['model']:<30} | {r['method']:<25} | {r['prompt']:>12,} | {r['cached']:>12,} | {r['output']:>12,} | {r['total']:>12,}"
            )
    else:
        summary = {}
        for r in results:
            key = (r["model"], r["method"])
            s = summary.setdefault(key, {"prompt": 0, "cached": 0, "output": 0, "total": 0, "count": 0})
            for k in ["prompt", "cached", "output", "total"]:
                s[k] += r[k]
            s["count"] += 1

        print(
            f"{'Model':<30} | {'Method':<25} | {'Reqs':>6} | {'Prompt':>12} | {'Cached':>12} | {'Output':>12} | {'Total':>12}"
        )
        print("-" * 125)
        total_all = {"prompt": 0, "cached": 0, "output": 0, "total": 0, "count": 0}
        for (m, meth), s in sorted(summary.items(), key=lambda x: x[1]["total"], reverse=True):
            print(
                f"{m:<30} | {meth:<25} | {s['count']:>6,} | {s['prompt']:>12,} | {s['cached']:>12,} | {s['output']:>12,} | {s['total']:>12,}"
            )
            for k in total_all:
                total_all[k] += s[k]
        print("-" * 125)
        print(
            f"{'TOTAL':<30} | {'':<25} | {total_all['count']:>6,} | {total_all['prompt']:>12,} | {total_all['cached']:>12,} | {total_all['output']:>12,} | {total_all['total']:>12,}"
        )


if __name__ == "__main__":
    main()
