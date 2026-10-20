"""Alerts on failed gates, stale sources, drift, pending human approvals and failed flow runs.

Always written to artifacts/alerts.json (the dashboard shows them as a banner). If ALERT_WEBHOOK_URL
is set in .env (Slack / Discord / Teams incoming webhook), each new alert is also posted there.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .. import config

ALERTS = config.ARTIFACTS / "alerts.json"


def from_quality(quality: dict, freshness: dict) -> list[dict]:
    out = []
    for d in quality.get("decisions", []):
        if d["action"] == "FAIL":
            out.append(dict(level="critical", kind="gate_failed", text=f"{d['gate']} {d['rule']}: {d['explanation']}"))
        elif d["status"] == "pending_approval":
            out.append(dict(level="warning", kind="needs_approval",
                            text=f"Rule {d['rule']} waits for human approval ({d['rows_affected']} rows)."))
        elif d["rule"] == "DRIFT-PSI" and d["action"] == "FLAG":
            out.append(dict(level="warning", kind="drift", text=d["explanation"]))
    for src, f in freshness.items():
        if f.get("late"):
            out.append(dict(level="warning", kind="stale_source",
                            text=f"{src} is late: newest data {f['newest']} ({f['age_h']:.0f} h old, limit {f['sla_h']} h)."))
    return out


def write(alerts: list[dict], run_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    alerts = [{**a, "run_id": run_id, "at_utc": now} for a in alerts]
    old = json.loads(ALERTS.read_text(encoding="utf-8")) if ALERTS.exists() else {}
    old_keys = {(a["kind"], a["text"]) for a in old.get("active", [])}
    ALERTS.write_text(json.dumps({"updated_utc": now, "active": alerts}, indent=2), encoding="utf-8")
    new = [a for a in alerts if (a["kind"], a["text"]) not in old_keys]
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if url and new:
        try:
            import requests
            text = "HackInsight pipeline alerts:\n" + "\n".join(f"[{a['level']}] {a['text']}" for a in new)
            requests.post(url, json={"text": text, "content": text}, timeout=15)
        except Exception as e:  # an alert channel being down must never break the pipeline
            print(f"  ! alert webhook failed: {e}")


def flow_failed(flow, flow_run, state) -> None:
    """Prefect on_failure hook: keep the current alerts and add the failed run."""
    cur =json.loads(ALERTS.read_text(encoding="utf-8")).get("active", []) if ALERTS.exists() else []
    cur.append(dict(level="critical", kind="flow_failed",
                    text=f"Hourly run {flow_run.name} failed: {str(state.message)[:300]}"))
    write(cur, str(flow_run.id))
