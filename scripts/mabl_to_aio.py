import os
import sys
import time
from xml.sax.saxutils import escape, quoteattr

import requests

MABL_API = "https://api.mabl.com"
AIO_API = "https://tcms.aiojiraapps.com/aio-tcms/api/v1"
SKIPPED_STATES = {"skipped", "cancelled"}


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"Missing required setting: {name}")
    return value


def csv_list(value):
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def trigger_deployment(api_key):
    body = {"environment_id": env("MABL_ENVIRONMENT_ID", required=True)}
    if env("MABL_APPLICATION_ID"):
        body["application_id"] = env("MABL_APPLICATION_ID")
    if csv_list(env("MABL_PLAN_IDS")):
        body["plan_ids"] = csv_list(env("MABL_PLAN_IDS"))
    if csv_list(env("MABL_PLAN_LABELS")):
        body["plan_labels"] = csv_list(env("MABL_PLAN_LABELS"))
    if env("GITHUB_SHA"):
        body["revision"] = env("GITHUB_SHA")
    resp = requests.post(f"{MABL_API}/events/deployment", auth=("key", api_key), json=body, timeout=30)
    resp.raise_for_status()
    event = resp.json()
    runs = event.get("triggered_plan_run_summaries") or []
    print(f"mabl deployment event {event['id']} started {len(runs)} plan run(s)")
    if not runs:
        sys.exit("No plans were triggered. Check the plan has a deployment trigger and matches the environment.")
    return event["id"]


def wait_for_results(api_key, event_id):
    poll = int(env("POLL_SECONDS", "15"))
    deadline = time.time() + int(env("TIMEOUT_MINUTES", "60")) * 60
    while time.time() < deadline:
        time.sleep(poll)
        resp = requests.get(f"{MABL_API}/execution/result/event/{event_id}", auth=("key", api_key), timeout=30)
        resp.raise_for_status()
        results = resp.json()
        if results.get("event_status"):
            return results
        print("Waiting for mabl plans to finish...")
    sys.exit("Timed out waiting for mabl results")


def build_junit(results):
    rows = []
    for execution in results.get("executions", []):
        names = {j.get("id"): j.get("name", "") for j in execution.get("journeys", [])}
        for run in execution.get("journey_executions", []):
            keys = [tc["id"].strip() for tc in run.get("test_cases") or [] if tc.get("id")]
            if not keys:
                continue
            start, stop = run.get("start_time") or 0, run.get("stop_time") or 0
            seconds = max(0, stop - start) / 1000
            if run.get("status") in SKIPPED_STATES:
                result = "<skipped/>"
            elif run.get("success"):
                result = ""
            else:
                msg = f"mabl status: {run.get('status')}. {run.get('app_href', '')}"
                result = f"<failure message={quoteattr(msg)}>{escape(msg)}</failure>"
            # AIO maps a result to a case when the testcase name starts with its key
            for key in keys:
                name = quoteattr(f"{key} {names.get(run.get('journey_id'), '')}".strip())
                rows.append(f'<testcase classname="mabl" name={name} time="{seconds:.3f}">{result}</testcase>')
    if not rows:
        return None
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="mabl" tests="{len(rows)}">\n' + "\n".join(rows) + "\n</testsuite>\n"


def push_to_aio(junit_xml):
    project, cycle = env("AIO_PROJECT_KEY", required=True), env("AIO_CYCLE_KEY", required=True)
    resp = requests.post(
        f"{AIO_API}/project/{project}/testcycle/{cycle}/import/results",
        params={"type": "Junit"},
        headers={"Authorization": f"AioAuth {env('AIO_API_TOKEN', required=True)}", "accept": "application/json"},
        data={"createNewRun": "true", "addCaseToCycle": "true", "createCase": "false"},
        files={"file": ("mabl-results.xml", junit_xml.encode("utf-8"), "text/xml")},
        timeout=60,
    )
    print(f"AIO import {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.json()


def write_summary(results, aio):
    path = env("GITHUB_STEP_SUMMARY")
    if not path:
        return
    m = results.get("journey_execution_metrics") or {}
    with open(path, "a", encoding="utf-8") as f:
        f.write("## mabl to AIO Tests\n\n")
        f.write(f"mabl tests: {m.get('total', 0)} run, {m.get('passed', 0)} passed, {m.get('failed', 0)} failed\n\n")
        if aio:
            f.write(f"AIO import: {aio.get('status')} ({aio.get('successCount', 0)} imported, {aio.get('errorCount', 0)} errors)\n")


def main():
    api_key = env("MABL_API_KEY", required=True)
    results = wait_for_results(api_key, trigger_deployment(api_key))
    junit_xml = build_junit(results)
    aio = None
    if junit_xml is None:
        print("No mabl results were linked to AIO case keys; nothing to import.")
    else:
        with open("mabl-results.xml", "w", encoding="utf-8") as f:
            f.write(junit_xml)
        aio = push_to_aio(junit_xml)
    write_summary(results, aio)
    if aio and aio.get("errorCount"):
        print(f"AIO rejected some results: {aio.get('errors')}")
    succeeded = (results.get("event_status") or {}).get("succeeded")
    if env("FAIL_ON_TEST_FAILURE", "true").lower() == "true" and not succeeded:
        sys.exit("One or more mabl tests failed")


if __name__ == "__main__":
    main()
