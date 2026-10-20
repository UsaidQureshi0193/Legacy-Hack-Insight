"""Command line entry point:  python -m hackinsight.cli <command>   (or `hi <command>`)

  data        full rebuild: empty working folders -> verify/fetch raw -> cleaned -> gold -> quality summary
  update      one incremental pipeline run (what the hourly flow does)
  train       baseline + model on gold, time split, writes artifacts/model_*.json
  index       build the Insight Agent's retrieval index from rag_library/
  ask         ask the Insight Agent a question         hi ask "Which districts ..." [--lang ur]
  approve     approve a high-risk quality rule           hi approve IDSR-R6 --by "Name"
  export-runs export Prefect flow-run history to prefect_runs.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hi")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("data")
    d.add_argument("--no-fetch", action="store_true", help="rebuild from raw files already on disk")
    sub.add_parser("update")
    sub.add_parser("train")
    sub.add_parser("index")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--lang", default="en", choices=["en", "ur"])
    ap = sub.add_parser("approve")
    ap.add_argument("rule")
    ap.add_argument("--by", required=True)
    rb = sub.add_parser("review-brief", help="a person marks a policy brief as reviewed before it is shared")
    rb.add_argument("brief_id", nargs="?", help="e.g. brief-malaria-Sindh-en; omit to list briefs")
    rb.add_argument("--by")
    ex = sub.add_parser("export-runs")
    ex.add_argument("--start", default="2026-10-20T11:00:00+00:00", help="UTC; default = Tue 20 Oct 4 PM PKT")
    ex.add_argument("--end", default="2026-10-21T11:00:00+00:00", help="UTC; default = Wed 21 Oct 4 PM PKT")
    ex.add_argument("--out", default="prefect_runs.csv")
    args = p.parse_args(argv)

    if args.cmd in ("data", "update"):
        from .pipeline import core
        status = core.run(full_rebuild=args.cmd == "data", fetch=not getattr(args, "no_fetch", False))
        core.print_summary(status)
        return 0 if status["last_run"]["status"] == "success" else 1
    if args.cmd == "train":
        from .model import train
        res = train.run()
        print(json.dumps(res["headline"], indent=2))
        return 0
    if args.cmd == "index":
        from .rag import index
        n = index.build()
        print(f"indexed {n} chunks")
        return 0
    if args.cmd == "ask":
        from .rag.agent import InsightAgent
        ans = InsightAgent().answer(args.question, lang=args.lang)
        print(ans.render())
        return 0
    if args.cmd == "approve":
        from . import config
        path = config.CONFIGS / "approvals.json"
        doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"approved": []}
        doc["approved"].append({"rule": args.rule, "by": args.by,
                                "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        print(f"approved {args.rule} by {args.by}")
        return 0
    if args.cmd == "review-brief":
        import hashlib
        from . import config
        briefs = {b["brief_id"]: b for b in json.loads((config.ARTIFACTS / "briefs.json").read_text(encoding="utf-8"))}
        path = config.CONFIGS / "brief_reviews.json"
        reviews = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not args.brief_id:
            for bid, b in briefs.items():
                r = reviews.get(bid)
                ok = r and r["text_sha1"] == hashlib.sha1(b["text"].encode()).hexdigest()
                print(f"{bid:40s} {'reviewed by ' + r['by'] if ok else 'NOT reviewed'}")
            return 0
        if args.brief_id not in briefs or not args.by:
            print("unknown brief id, or --by missing")
            return 1
        b = briefs[args.brief_id]
        print(b["text"])
        reviews[args.brief_id] = {"by": args.by, "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "text_sha1": hashlib.sha1(b["text"].encode()).hexdigest()}
        path.write_text(json.dumps(reviews, indent=2), encoding="utf-8")
        print(f"\nmarked reviewed by {args.by}")
        return 0
    if args.cmd == "export-runs":
        from .pipeline import export_runs
        n = export_runs.export(args.start, args.end, args.out)
        print(f"wrote {n} runs to {args.out}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
