"""Example CLI tool — demonstrates the CLI-first tool pattern."""

import argparse
import json
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Example tool: echo with formatting")
    parser.add_argument("--text", required=True, help="Text to process")
    parser.add_argument(
        "--format",
        choices=["json", "text", "upper"],
        default="text",
        help="Output format",
    )
    args = parser.parse_args()

    if args.format == "json":
        json.dump({"result": args.text, "length": len(args.text)}, sys.stdout)
    elif args.format == "upper":
        print(args.text.upper())
    else:
        print(args.text)


if __name__ == "__main__":
    main()
