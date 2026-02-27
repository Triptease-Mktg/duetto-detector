import argparse
import asyncio
import sys
from urllib.parse import urlparse

from pipeline.csv_processor import parse_csv, results_to_csv
from pipeline.batch_runner import run_batch


def main():
    parser = argparse.ArgumentParser(
        prog="duetto-detector",
        description="Detect Duetto products on hotel booking engines",
    )
    parser.add_argument(
        "csv_file", nargs="?", help="Path to CSV file (columns: name,website)"
    )
    parser.add_argument(
        "--url", help="Single hotel website URL to scan"
    )
    parser.add_argument(
        "--name", help="Hotel name (used with --url)"
    )
    parser.add_argument(
        "--city", help="Hotel city (used with --url for AI booking link discovery)"
    )
    parser.add_argument(
        "-o", "--output", default="results.csv", help="Output CSV path"
    )
    parser.add_argument(
        "-c", "--concurrent", type=int, default=3,
        help="Max concurrent scans",
    )
    parser.add_argument(
        "--screenshots", help="Directory to save booking engine screenshots"
    )

    args = parser.parse_args()

    if args.url:
        url = args.url
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        name = args.name or urlparse(url).netloc.replace("www.", "")
        city = args.city or ""
        hotels = [{"name": name, "website": url, "city": city}]
    elif args.csv_file:
        with open(args.csv_file, "r") as f:
            hotels = parse_csv(f.read())
    else:
        parser.error("Provide a CSV file or use --url")
        return

    if not hotels:
        print("No valid hotels found.")
        sys.exit(1)

    total = len(hotels)
    print(f"Scanning {total} hotels for Duetto products...\n")

    def progress(index, name, status):
        if status == "scanning":
            print(f"  [{index + 1}/{total}] Scanning: {name}")
        elif status == "done":
            print(f"  [{index + 1}/{total}] Done: {name}")

    result = asyncio.run(
        run_batch(
            hotels,
            max_concurrent=args.concurrent,
            screenshot_dir=args.screenshots,
            on_progress=progress,
        )
    )

    csv_output = results_to_csv(result)
    with open(args.output, "w") as f:
        f.write(csv_output)

    print(f"\n{'=' * 50}")
    print(f"Results saved to {args.output}")
    print(f"  Total scanned:  {result.scanned}")
    print(f"  Duetto Pixel:   {result.duetto_pixel_count}")
    print(f"  GameChanger:    {result.gamechanger_count}")
    print(f"{'=' * 50}")

    # Quick summary table
    for r in result.results:
        products = ", ".join(p.value for p in r.duetto_products)
        status = "DUETTO" if r.duetto_pixel_detected or r.gamechanger_detected else "-"
        competitors = ", ".join(c.vendor for c in r.competitor_rms) if r.competitor_rms else ""
        line = f"  {status:8s} | {r.hotel_name} | {products}"
        if competitors:
            line += f" | Other: {competitors}"
        print(line)


if __name__ == "__main__":
    main()
