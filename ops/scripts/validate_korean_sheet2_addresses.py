from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Korean address candidates against Kakao geocoding.")
    parser.add_argument("workbook", help="Path to workbook")
    parser.add_argument("--sheet", default="Sheet2", help="Sheet name to inspect")
    parser.add_argument("--header-row", type=int, default=1, help="0-based header row index for pandas")
    parser.add_argument("--column", default="Bus Stop", help="Column containing Korean addresses")
    parser.add_argument("--country", default="South Korea")
    parser.add_argument("--city", default="Seoul")
    parser.add_argument("--english-column", default="", help="Optional English companion column")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit on unique addresses")
    parser.add_argument("--timeout", type=int, default=5, help="Per-request timeout seconds")
    parser.add_argument("--output-prefix", default="tmp/kakao_sheet2_validation", help="Prefix for output files")
    args = parser.parse_args()

    for key in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]:
        os.environ.pop(key, None)

    import sys

    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "apps" / "client"))

    import client_runtime
    from client_runtime import kakao_geocode_query, normalize_korean_candidate_address

    client_runtime.REQUEST_TIMEOUT = int(args.timeout)

    workbook = Path(args.workbook).expanduser().resolve()
    df = pd.read_excel(workbook, sheet_name=args.sheet, header=args.header_row)
    raw_rows = []
    english_column = args.english_column.strip()
    for _, row in df.iterrows():
        value = row.get(args.column)
        text = str(value).strip() if pd.notna(value) else ""
        if not text or text.lower() == "nan":
            continue
        english_text = ""
        if english_column:
            english_value = row.get(english_column)
            english_text = str(english_value).strip() if pd.notna(english_value) else ""
        raw_rows.append({"raw_value": text, "english_value": english_text})

    normalized_rows = []
    seen = set()
    for item in raw_rows:
        raw = item["raw_value"]
        normalized = normalize_korean_candidate_address(raw)
        standardized = normalized["standardized"].strip()
        if not standardized or standardized in seen:
            continue
        seen.add(standardized)
        normalized_rows.append(
            {
                "raw_value": raw,
                "english_value": item["english_value"],
                "cleaned_no_route_tag": normalized["cleaned"],
                "standardized_candidate": standardized,
            }
        )

    if args.limit > 0:
        normalized_rows = normalized_rows[: args.limit]

    results = []
    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)

    for index, row in enumerate(normalized_rows, start=1):
        candidate = row["standardized_candidate"]
        try:
            point = kakao_geocode_query(args.country, args.city, candidate)
            results.append(
                {
                    **row,
                    "ok": True,
                    "formatted_address": point.get("formatted_address"),
                    "lat": point.get("lat"),
                    "lng": point.get("lng"),
                    "provider": point.get("provider"),
                }
            )
        except Exception as exc:  # pragma: no cover - network behavior varies
            results.append(
                {
                    **row,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

        if index % 10 == 0 or index == len(normalized_rows):
            result_df = pd.DataFrame(results)
            result_df.to_csv(output_prefix.with_name(f"{output_prefix.name}_results.csv"), index=False)
            result_df[result_df["ok"] == False].to_csv(  # noqa: E712
                output_prefix.with_name(f"{output_prefix.name}_failures.csv"),
                index=False,
            )
            print(
                f"progress {index}/{len(normalized_rows)} ok={sum(1 for item in results if item['ok'])} "
                f"fail={sum(1 for item in results if not item['ok'])}",
                flush=True,
            )

    with output_prefix.with_name(f"{output_prefix.name}_results.json").open("w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)

    summary = {
        "workbook": str(workbook),
        "sheet": args.sheet,
        "column": args.column,
        "english_column": english_column,
        "unique_candidates": len(normalized_rows),
        "ok_count": sum(1 for item in results if item["ok"]),
        "fail_count": sum(1 for item in results if not item["ok"]),
    }
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
