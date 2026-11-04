"""Compatibility shim: the CLI lives in the loom.cli package itself."""

from loom import cli

main = cli.main

__all__ = ["main"]
