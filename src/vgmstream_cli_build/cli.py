from __future__ import annotations

import argparse
from pathlib import Path

from .injector import DEFAULT_AUDIT_PATCH, InjectorPaths, apply_overlay, export_audit_patch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vgmstream-cli-build")
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply", help="Copy overlay files and inject hooks into an upstream checkout.")
    apply_parser.add_argument("--repo-root", type=Path, required=True, help="Path to the cloned upstream vgmstream repository.")
    apply_parser.add_argument("--workspace-root", type=Path, default=Path("."), help="Path to this workspace root containing overlay/ and audit/.")

    export_parser = subparsers.add_parser("export-audit", help="Export the current injected state as an audit patch snapshot.")
    export_parser.add_argument("--repo-root", type=Path, required=True, help="Path to the injected upstream repository.")
    export_parser.add_argument("--workspace-root", type=Path, default=Path("."), help="Path to this workspace root.")
    export_parser.add_argument("--audit-output", type=Path, default=DEFAULT_AUDIT_PATCH, help="Path to the audit patch output, relative to workspace root by default.")

    sync_parser = subparsers.add_parser("sync", help="Apply the overlay and refresh the audit patch in one command.")
    sync_parser.add_argument("--repo-root", type=Path, required=True, help="Path to the cloned upstream repository.")
    sync_parser.add_argument("--workspace-root", type=Path, default=Path("."), help="Path to this workspace root.")
    sync_parser.add_argument("--audit-output", type=Path, default=DEFAULT_AUDIT_PATCH, help="Path to the audit patch output, relative to workspace root by default.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    paths = InjectorPaths.resolve(
        workspace_root=args.workspace_root,
        repo_root=args.repo_root,
        audit_output=getattr(args, "audit_output", None),
    )

    if args.command == "apply":
        apply_overlay(paths)
        print(f"Overlay applied to {paths.repo_root}")
        return

    if args.command == "export-audit":
        audit_path = export_audit_patch(paths)
        print(f"Audit patch exported to {audit_path}")
        return

    if args.command == "sync":
        apply_overlay(paths)
        audit_path = export_audit_patch(paths)
        print(f"Overlay applied to {paths.repo_root}")
        print(f"Audit patch exported to {audit_path}")
        return

    raise AssertionError(f"Unhandled command: {args.command}")
