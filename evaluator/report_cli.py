"""
Usage: python3 -m evaluator.report_cli --gen-id <gen_id> [--output-dir <dir>] [--no-upload]

Reads S3 config from env vars: S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import boto3

from evaluator.report import generate_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a SoTA@Home generation report")
    parser.add_argument("--gen-id", required=True, help="Generation ID")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Local output directory (default: /tmp/reports/<gen_id>)",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip uploading to S3",
    )
    args = parser.parse_args()

    gen_id = args.gen_id
    output_dir = Path(args.output_dir) if args.output_dir else Path(f"/tmp/reports/{gen_id}")

    endpoint = os.environ.get("S3_ENDPOINT_URL")
    access_key = os.environ.get("S3_ACCESS_KEY")
    secret_key = os.environ.get("S3_SECRET_KEY")

    if not endpoint or not access_key or not secret_key:
        logger.error("S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY must be set")
        sys.exit(1)

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    zip_path = generate_report(s3, gen_id, output_dir, upload=not args.no_upload)
    print(f"Local zip: {zip_path}")
    if args.no_upload:
        print("Upload skipped (--no-upload)")
    else:
        print(f"Uploaded: s3://runs/reports/{gen_id}/report.zip")


if __name__ == "__main__":
    main()
