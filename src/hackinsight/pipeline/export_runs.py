"""Export the Prefect flow-run history (unedited) to prefect_runs.csv for the judged window."""
from __future__ import annotations

import asyncio
import csv
from datetime import datetime

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterStartTime
from prefect.client.schemas.sorting import FlowRunSort

COLS = ["flow_run_id", "flow_run_name", "flow_name", "deployment_id", "state_type", "state_name",
        "expected_start_time", "start_time", "end_time", "total_run_time_s", "run_count", "tags"]


async def _read(start: datetime, end: datetime):
    async with get_client() as client:
        runs = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(start_time=FlowRunFilterStartTime(after_=start, before_=end)),
            sort=FlowRunSort.START_TIME_ASC, limit=1000)
        names = {}
        for r in runs:
            if r.flow_id not in names:
                names[r.flow_id] = (await client.read_flow(r.flow_id)).name
        return runs, names


def export(start: str, end: str, out: str) -> int:
    runs, names = asyncio.run(_read(datetime.fromisoformat(start), datetime.fromisoformat(end)))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in runs:
            w.writerow([r.id, r.name, names.get(r.flow_id), r.deployment_id, r.state_type.value if r.state_type else None,
                        r.state_name, r.expected_start_time, r.start_time, r.end_time,
                        r.total_run_time.total_seconds() if r.total_run_time else None, r.run_count,
                        ";".join(r.tags or [])])
    return len(runs)
