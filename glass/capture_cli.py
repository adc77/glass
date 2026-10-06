"""Explicit lab setup, live sample operation, capture import, and supervised replay."""

import argparse

from seam.canon import dumps
from seam.errors import Refuse

from glass.capture import read_bundle, run_bundle, write_case
from glass.capture_http import serve
from glass.lab import initial_lab
from glass.live_capture import Service


def main():
    parser = argparse.ArgumentParser(description="Glass live capture and Seam replay")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--db", required=True)
    init.add_argument("--busy-count", type=int, default=1)
    init.add_argument("--keep-report-ack", action="store_true")
    init.add_argument("--qc-max", type=int, default=100)
    live = commands.add_parser("serve")
    live.add_argument("--db", required=True)
    live.add_argument("--port", type=int, default=8766)
    for name in ("import", "replay"):
        command = commands.add_parser(name)
        command.add_argument("--bundle", required=True)
        command.add_argument("--out", required=True)
        command.add_argument("--reading-delay-ns", type=int, default=0)
    args = parser.parse_args()
    try:
        if args.command == "init":
            Service(
                args.db,
                initial_lab=initial_lab(
                    busy_remaining=args.busy_count,
                    lose_ack=not args.keep_report_ack,
                    qc_max=args.qc_max,
                ),
            ).close()
        elif args.command == "serve":
            service = Service(args.db)
            try:
                serve(service, args.port)
            finally:
                service.close()
        else:
            document = read_bundle(args.bundle)
            if args.command == "import":
                print(
                    write_case(
                        document, args.out, reading_delay_ns=args.reading_delay_ns
                    )
                )
            else:
                result = run_bundle(
                    document, args.out, reading_delay_ns=args.reading_delay_ns
                )
                print(
                    dumps(
                        {
                            "status": result.artifact["status"],
                            "artifact": result.artifact_path,
                            "digest": result.artifact.get("digest"),
                        }
                    )
                )
                return result.returncode
    except (ValueError, OSError, Refuse) as err:
        parser.exit(2, f"glass: {err}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
