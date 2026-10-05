"""Download public FRED credit vintages; write only ignored research inputs.

Uses the existing FRED_API_KEY without logging it. No DB connection, registry
edits, portfolio changes or production fetcher calls.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from dotenv import dotenv_values

SERIES = "QUSPAM770A"
AS_OF = "2026-10-04"
DIRECTORY = Path(__file__).resolve().parent
REPO = DIRECTORY.parents[2]


def request(endpoint: str, key: str, **parameters: str | int) -> dict:
    params = {"api_key": key, "file_type": "json", "series_id": SERIES, **parameters}
    url = f"https://api.stlouisfed.org/fred/series/{endpoint}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise SystemExit(f"FRED {endpoint}: HTTP {error.code}; URL/key omitted") from None
    except URLError as error:
        raise SystemExit(f"FRED {endpoint}: {error.reason}; URL/key omitted") from None


def main() -> None:
    key = dotenv_values(REPO / ".env").get("FRED_API_KEY")
    if not key:
        raise SystemExit("FRED_API_KEY unavailable")
    output = DIRECTORY / "data"
    output.mkdir(exist_ok=True)
    requests = {
        "vintage_dates.json": (
            "vintagedates",
            {"realtime_start": "1776-07-04", "realtime_end": AS_OF},
        ),
        "credit_vintages.json": (
            "observations",
            {
                "realtime_start": "1776-07-04",
                "realtime_end": AS_OF,
                "observation_start": "1940-01-01",
                "output_type": 2,
                "limit": 100000,
            },
        ),
        "credit_latest.json": (
            "observations",
            {
                "realtime_start": AS_OF,
                "realtime_end": AS_OF,
                "observation_start": "1940-01-01",
                "output_type": 1,
                "limit": 100000,
            },
        ),
    }
    manifest = {
        "series": SERIES,
        "as_of": AS_OF,
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "files": {},
    }
    for name, (endpoint, params) in requests.items():
        path = output / name
        if path.exists():
            raise SystemExit(f"Refuse to overwrite frozen input: {path.name}")
        payload = request(endpoint, key, **params)
        if "observations" in payload and len(payload["observations"]) != payload["count"]:
            raise SystemExit("FRED response truncated; do not proceed with partial vintages")
        path.write_text(json.dumps(payload, indent=2) + "\n")
        rows = payload.get("observations", payload.get("vintage_dates", []))
        manifest["files"][name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows": len(rows),
            "endpoint": endpoint,
            "parameters": params,
        }
        print(f"Downloaded {name}: {len(rows)} rows", flush=True)
        if rows and isinstance(rows[0], dict):
            print(f"First/last row: {rows[0]} / {rows[-1]}", flush=True)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
