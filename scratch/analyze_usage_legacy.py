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
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = result.stdout.splitlines()
    except Exception as e:
        print(f"Error running journalctl: {e}")
        return []

    data = []
    current_req = {}

    # Regex patterns
    # 1. New consolidated format: [DONE] {method} {url} ({duration}) Token Usage: ...
    # 2. Intermediate format: [DONE] {method} {url} | Tokens: ...
    # 3. Old multi-line format: [USAGE] Tokens: ... (after a DONE line)

    # Combined usage regex for K=V pairs
    usage_kv_re = re.compile(r"(Prompt|P|Cached|ca|Output|O|Thoughts|th|Total|T)=([0-9]+)")

    current_req = None
    current_usage = None

    for line in lines:
        # Extract Model and Method from URL
        # Pattern covers:
        # [DONE] POST https://.../models/NAME:METHOD?QUERY
        # Proxying (Stream) [200]: POST https://.../models/NAME:METHOD
        model_match = re.search(
            r"(?:\[DONE\]|Proxying.*?\]:?)\s+(?:GET|POST)\s+https://.*?/models/([^?:\s]+)(?::([^?:\s]+))?", line
        )

        if model_match:
            model, method = model_match.groups()
            method = method or "unknown"

            # If we had a previous request with usage, save it now (deduplication)
            if current_req and current_usage:
                data.append({**current_req, "usage": current_usage})

            current_req = {"model": model, "method": method}
            current_usage = None

            # Check if usage is in the same line (latest format)
            if "Tokens:" in line or "Token Usage:" in line:
                current_usage = {m[0]: int(m[1]) for m in usage_kv_re.findall(line)}
            continue

        # Check for standalone USAGE lines (older format)
        if "[USAGE] Tokens:" in line or "Token Usage:" in line:
            usage = {m[0]: int(m[1]) for m in usage_kv_re.findall(line)}
            if usage:
                # Update current_usage (monotonic increase in streams)
                current_usage = usage

    # Final entry
    if current_req and current_usage:
        data.append({**current_req, "usage": current_usage})

    return data


def normalize_usage(u):
    return {
        "prompt": u.get("Prompt") or u.get("P") or 0,
        "cached": u.get("Cached") or u.get("ca") or 0,
        "output": u.get("Output") or u.get("O") or 0,
        "thoughts": u.get("Thoughts") or u.get("th") or 0,
        "total": u.get("Total") or u.get("T") or 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Analyze aistudio-bridge token usage")
    parser.add_argument("--since", help="journalctl --since (e.g. 'today', '2026-05-05 12:00:00')")
    parser.add_argument("--until", help="journalctl --until")
    parser.add_argument("--all", action="store_true", help="Show individual requests instead of aggregated summary")
    args = parser.parse_args()

    results = parse_logs(since=args.since or "today", until=args.until)

    if not results:
        print("No usage data found.")
        return

    if args.all:
        print(
            f"{'Model':<30} | {'Method':<25} | {'Prompt':>10} | {'Cached':>10} | {'Output':>10} | {'Thoughts':>10} | {'Total':>10}"
        )
        print("-" * 125)
        for entry in results:
            m = entry["model"]
            meth = entry["method"]
            u = normalize_usage(entry["usage"])
            print(
                f"{m:<30} | {meth:<25} | {u['prompt']:>10,} | {u['cached']:>10,} | {u['output']:>10,} | {u['thoughts']:>10,} | {u['total']:>10,}"
            )
    else:
        # Aggregate by (model, method)
        summary = {}
        for entry in results:
            key = (entry["model"], entry["method"])
            if key not in summary:
                summary[key] = {"prompt": 0, "cached": 0, "output": 0, "thoughts": 0, "total": 0, "count": 0}

            u = normalize_usage(entry["usage"])
            for k in u:
                summary[key][k] += u[k]
            summary[key]["count"] += 1

        print(
            f"{'Model':<30} | {'Method':<25} | {'Reqs':>6} | {'Prompt':>12} | {'Cached':>12} | {'Output':>12} | {'Total':>12}"
        )
        print("-" * 125)

        # Sort by total tokens descending
        sorted_keys = sorted(summary.keys(), key=lambda k: summary[k]["total"], reverse=True)

        total_all = {"prompt": 0, "cached": 0, "output": 0, "total": 0, "count": 0}
        for key in sorted_keys:
            m, meth = key
            s = summary[key]
            print(
                f"{m:<30} | {meth:<25} | {s['count']:>6,} | {s['prompt']:>12,} | {s['cached']:>12,} | {s['output']:>12,} | {s['total']:>12,}"
            )
            for k in ["prompt", "cached", "output", "total"]:
                total_all[k] += s[k]
            total_all["count"] += s["count"]

        print("-" * 125)
        print(
            f"{'TOTAL':<30} | {'':<25} | {total_all['count']:>6,} | {total_all['prompt']:>12,} | {total_all['cached']:>12,} | {total_all['output']:>12,} | {total_all['total']:>12,}"
        )


if __name__ == "__main__":
    main()
