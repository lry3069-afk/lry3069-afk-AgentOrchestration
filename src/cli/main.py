"""CLI entry point for the agent orchestrator."""

import argparse
import sys

from src.common.config import Config
from src.common.logging import configure_logging


def cli():
    parser = argparse.ArgumentParser(description="Agent Orchestrator CLI")
    parser.add_argument("--config", "-c", help="Path to config file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    init_parser = subparsers.add_parser("init", help="Initialize a new project")
    init_parser.add_argument("name", help="Project name")

    deploy_parser = subparsers.add_parser("deploy", help="Deploy an agent")
    deploy_parser.add_argument("manifest", help="Path to agent manifest file")

    status_parser = subparsers.add_parser("status", help="Show agent status")
    status_parser.add_argument("--watch", "-w", action="store_true", help="Watch mode")

    logs_parser = subparsers.add_parser("logs", help="View agent logs")
    logs_parser.add_argument("agent_id", help="Agent ID")
    logs_parser.add_argument("--tail", "-t", type=int, default=50, help="Number of lines")

    args = parser.parse_args()

    if args.verbose:
        configure_logging("DEBUG")
    else:
        configure_logging("INFO")

    if args.command == "init":
        print(f"Initializing project: {args.name}")
    elif args.command == "deploy":
        print(f"Deploying agent from manifest: {args.manifest}")
    elif args.command == "status":
        print("Checking agent status...")
    elif args.command == "logs":
        print(f"Fetching logs for agent: {args.agent_id}")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    cli()

# 2019-01-03T18:44:00 update

# 2019-01-15T19:36:16 update

# 2019-02-15T12:13:23 update

# 2019-03-18T20:23:13 update

# 2019-03-22T09:42:46 update

# 2019-03-25T09:42:45 update

# 2019-07-16T18:56:48 update

# 2019-07-25T19:52:16 update

# 2019-08-18T18:35:47 update

# 2019-10-08T08:27:44 update

# 2019-11-05T14:16:14 update

# 2019-12-06T15:08:55 update

# 2020-01-15T12:28:12 update

# 2020-02-18T12:59:12 update

# 2020-03-18T18:36:09 update

# 2020-03-31T11:11:42 update

# 2020-06-16T08:24:25 update

# 2020-07-08T18:35:39 update

# 2020-12-09T10:37:56 update

# 2020-12-18T09:38:50 update

# 2020-12-29T13:08:30 update

# 2021-01-01T10:07:30 update

# 2021-01-19T16:42:27 update
