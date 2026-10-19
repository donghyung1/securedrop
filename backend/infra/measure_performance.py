"""Measure PRD chapter 7 latency targets and size limits against the deployed API.

Run from backend: uv run python infra/measure_performance.py
Run it after the function has been idle (~15 min) so the first call is a cold start.
Times are client-side wall clock and include the caller's network; transfers are revoked.
"""

import hashlib
import json
import random
import statistics
import time
from pathlib import Path

import boto3
import httpx

OUT = Path(__file__).resolve().parents[1] / "deploy" / "aws"
STATE = json.loads((OUT / "serverless.json").read_text())
CONFIG = json.loads((OUT / "resources.json").read_text())
MIB = 1024 * 1024
TARGETS = {
    "cold_start": 3,
    "text_scan_warm": 1,
    "file_scan_4mib": 2,
    "csv_sanitize_1mib": 3,
    "transfer_20mib": 10,
}


def text_of(size, seed=0):
    rng = random.Random(seed)
    words = ["보고서", "회의", "일정", "프로젝트", "status", "update", "draft", "review"]
    lines, total = [], 0
    while total < size:
        line = " ".join(rng.choice(words) for _ in range(12))
        if len(lines) % 500 == 0:
            line += " 연락처 010-1234-5678"
        lines.append(line)
        total += len(line.encode()) + 1
    data = "\n".join(lines).encode()[:size].decode(errors="ignore").encode()
    return data + b" " * (size - len(data))


def csv_of(size):
    rows, total, i = ["name,phone,email,memo"], 0, 0
    while total < size:
        row = f"user{i},010-{1000 + i % 9000}-{2000 + i % 7000},user{i}@example.com,메모 {i}"
        rows.append(row)
        total += len(row.encode()) + 1
        i += 1
    return "\n".join(rows).encode()[:size].rsplit(b"\n", 1)[0] + b"\n"


def timed(fn):
    start = time.perf_counter()
    value = fn()
    return time.perf_counter() - start, value


def main():
    results, measured = {}, {}
    with httpx.Client(
        base_url=STATE["api_base"], timeout=60, headers={"Origin": STATE["url"]}
    ) as c:

        def ok(response, expected=200):
            assert response.status_code == expected, (response.status_code, response.text[:200])
            return response.json() if response.content else {}

        measured["cold_start"], _ = timed(lambda: ok(c.get("/api/health")))
        samples = [
            timed(
                lambda: ok(c.post("/api/scan", json={"text": "회의록 " * 300 + "010-1234-5678"}))
            )[0]
            for _ in range(10)
        ]
        measured["text_scan_warm"] = statistics.median(samples)
        results["text_scan_warm_samples"] = [round(s, 3) for s in samples]

        content = text_of(4 * MIB)
        measured["file_scan_4mib"], scan = timed(
            lambda: ok(
                c.post("/api/scan/file", files={"file": ("perf.txt", content, "text/plain")})
            )
        )
        assert scan["blocked"]
        over = content + b"x"
        results["file_scan_over_4mib"] = c.post(
            "/api/scan/file", files={"file": ("perf.txt", over, "text/plain")}
        ).status_code

        csv = csv_of(MIB)
        measured["csv_sanitize_1mib"], clean = timed(
            lambda: ok(
                c.post(
                    "/api/sanitize",
                    files={"file": ("perf.csv", csv, "text/csv")},
                    data={
                        "actions": json.dumps([{"action": "MASK", "targets": ["PHONE", "EMAIL"]}])
                    },
                )
            )
        )
        assert clean["clean"], clean["findings_after"]

        big = random.Random(1).randbytes(20 * MIB)
        meta = {
            "name": "perf.pdf",
            "size": len(big),
            "type": "application/pdf",
            "sha256": hashlib.sha256(big).hexdigest(),
        }
        body = {"files": [meta], "acknowledge_unsupported": True, "expires_in": 300}
        results["transfer_over_20mib"] = c.post(
            "/api/transfers", json={**body, "files": [{**meta, "size": 20 * MIB + 1}]}
        ).status_code
        ids = []

        def transfer():
            pending = ok(c.post("/api/transfers", json=body))
            ids.append((pending["id"], pending["manage_token"]))
            upload = pending["uploads"][0]
            response = httpx.post(
                upload["url"],
                data=upload["fields"],
                files={"file": (meta["name"], big, meta["type"])},
                headers={"Origin": STATE["url"]},
                timeout=120,
            )
            assert response.status_code == 204, (response.status_code, response.text[:200])
            return ok(
                c.post(
                    f"/api/transfers/{pending['id']}/complete",
                    headers={"X-Manage-Token": pending["manage_token"]},
                )
            )

        measured["transfer_20mib"], done = timed(transfer)
        assert done["status"] == "ACTIVE"

        # Declared-size enforcement by the signed S3 POST policy (content-length-range).
        small = b"a" * 1024
        smeta = {**meta, "size": len(small), "sha256": hashlib.sha256(small).hexdigest()}
        pending = ok(c.post("/api/transfers", json={**body, "files": [smeta]}))
        ids.append((pending["id"], pending["manage_token"]))
        upload = pending["uploads"][0]
        response = httpx.post(
            upload["url"],
            data=upload["fields"],
            files={"file": ("perf.pdf", small + b"a", "application/pdf")},
            timeout=60,
        )
        code = (
            response.text.split("<Code>")[1].split("</Code>")[0]
            if "<Code>" in response.text
            else ""
        )
        results["s3_post_over_declared_size"] = f"{response.status_code} {code}"

        for identifier, token in ids:
            c.delete(f"/api/transfers/{identifier}", headers={"X-Manage-Token": token})

    logs = boto3.Session(profile_name=CONFIG["profile"], region_name=STATE["region"]).client("logs")
    inits = []
    kwargs = {
        "logGroupName": "/aws/lambda/securedrop-api",
        "filterPattern": '"Init Duration"',
        "startTime": int((time.time() - 14 * 86400) * 1000),
    }
    while True:
        page = logs.filter_log_events(**kwargs)
        inits += [
            float(e["message"].split("Init Duration: ")[1].split(" ms")[0]) for e in page["events"]
        ]
        if not page.get("nextToken"):
            break
        kwargs["nextToken"] = page["nextToken"]
    if inits:
        results["lambda_init_ms"] = {
            "count": len(inits),
            "median": round(statistics.median(inits)),
            "max": round(max(inits)),
        }
    for name, seconds in measured.items():
        results[name] = {
            "seconds": round(seconds, 3),
            "target": TARGETS[name],
            "passed": seconds <= TARGETS[name],
        }
    results["measured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    (OUT / "performance-verification.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
