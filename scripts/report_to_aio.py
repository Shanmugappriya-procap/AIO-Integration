import json
import os
import sys
from xml.sax.saxutils import escape, quoteattr

import requests

AIO_API = "https://tcms.aiojiraapps.com/aio-tcms/api/v1"


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"Missing required setting: {name}")
    return value


def build_junit(results):
    rows = []
    for r in results:
        if r["passed"]:
            body = ""
        else:
            msg = f"mabl local run failed for test {r['mabl_test_id']}"
            body = f"<failure message={quoteattr(msg)}>{escape(r.get('log', ''))}</failure>"
        # AIO maps a result to a case when the testcase name starts with its key
        for key in r["aio_keys"]:
            name = quoteattr(f"{key} {r['name']}")
            rows.append(f'<testcase classname="mabl-local" name={name} time="{r["seconds"]:.3f}">{body}</testcase>')
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="mabl-local" tests="{len(rows)}">\n' + "\n".join(rows) + "\n</testsuite>\n"


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
    with open(path, "a", encoding="utf-8") as f:
        f.write("## mabl local run to AIO Tests\n\n| Test | AIO keys | Result | Time |\n| --- | --- | --- | --- |\n")
        for r in results:
            f.write(f"| {r['name']} | {', '.join(r['aio_keys'])} | {'Passed' if r['passed'] else 'Failed'} | {r['seconds']:.0f}s |\n")
        f.write(f"\nAIO import: {aio.get('status')} ({aio.get('successCount', 0)} imported, {aio.get('errorCount', 0)} errors)\n")


def main():
    with open(env("RESULTS_FILE", "mabl-local-results.json"), encoding="utf-8") as f:
        results = json.load(f)
    junit_xml = build_junit(results)
    with open("mabl-results.xml", "w", encoding="utf-8") as f:
        f.write(junit_xml)
    aio = push_to_aio(junit_xml)
    write_summary(results, aio)
    if aio.get("errorCount"):
        print(f"AIO rejected some results: {aio.get('errors')}")
    if not all(r["passed"] for r in results):
        sys.exit("One or more mabl tests failed")


if __name__ == "__main__":
    main()
