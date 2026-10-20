# HackInsight 2026 - Pakistan malaria & dengue early warning
# Windows without make:  .\make.ps1 data   (same targets)

PY ?= .venv/bin/python
ifeq ($(OS),Windows_NT)
PY = .venv/Scripts/python.exe
endif

.PHONY: setup data update train index app server flow test export-runs all

setup:            ## create the environment (Python 3.12)
	uv venv --python 3.12 .venv && uv pip install --python $(PY) -e .

data:             ## empty working folders -> verify/fetch raw -> cleaned -> gold -> quality summary
	$(PY) -m hackinsight.cli data

update:           ## one incremental run (what the hourly flow does)
	$(PY) -m hackinsight.cli update

train:            ## baseline + model, time split, metrics by group
	$(PY) -m hackinsight.cli train

index:            ## build the Insight Agent retrieval index
	$(PY) -m hackinsight.cli index

all: data train index

app:              ## run the dashboard locally
	$(PY) app/app.py

server:           ## persistent Prefect server (keeps run history for prefect_runs.csv)
	.venv/bin/prefect server start --host 127.0.0.1 --port 4200

flow:             ## serve the hourly Prefect deployment (cron 5 * * * *)
	PREFECT_API_URL=http://127.0.0.1:4200/api $(PY) -m hackinsight.pipeline.flow

test:
	$(PY) -m pytest -q

export-runs:      ## prefect_runs.csv for Tue 20 Oct 4 PM -> Wed 21 Oct 4 PM PKT
	PREFECT_API_URL=http://127.0.0.1:4200/api $(PY) -m hackinsight.cli export-runs
