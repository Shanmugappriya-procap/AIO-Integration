import json
import os
import shutil
import subprocess
import sys
import time


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"Missing required setting: {name}")
    return value


def main():
    mabl = shutil.which("mabl")
    if not mabl:
        sys.exit("mabl CLI not found. Install it with: npm install -g @mablhq/mabl-cli, then restart the runner")
    key = env("MABL_CLI_API_KEY")
    if key and subprocess.run([mabl, "auth", "activate-key", key], capture_output=True).returncode != 0:
        sys.exit("Could not authenticate the mabl CLI. Check the MABL_CLI_API_KEY secret.")

    with open(env("TEST_MAP_FILE", "tests.json"), encoding="utf-8") as f:
        tests = json.load(f)

    results = []
    for test in tests:
        cmd = [mabl, "tests", "run", "--id", test["mabl_test_id"],
               "--environment-id", env("MABL_ENVIRONMENT_ID", required=True),
               "--application-id", env("MABL_APPLICATION_ID", required=True)]
        if (env("MABL_HEADLESS") or "true").lower() != "false":
            cmd.append("--headless")
        print(f"\n=== Running {test['name']} ({test['mabl_test_id']}) on this machine ===", flush=True)
        started = time.time()
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(done.stdout, done.stderr, sep="\n", flush=True)
        passed = done.returncode == 0
        seconds = time.time() - started
        print(f"=== {'PASSED' if passed else 'FAILED'} in {seconds:.0f}s ===", flush=True)
        log_tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-15:])
        results.append({**test, "passed": passed, "seconds": round(seconds, 1), "log": log_tail})

    with open("mabl-local-results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved {len(results)} result(s) to mabl-local-results.json")


if __name__ == "__main__":
    main()
