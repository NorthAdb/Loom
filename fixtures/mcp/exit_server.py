"""A stdio MCP server that exits immediately: the crash-path fixture.

Prints one stderr line, then returns; stdout closes and the client must
report the exit instead of hanging.
"""

import sys


def main() -> None:
    print("exit server: leaving at once", file=sys.stderr)


if __name__ == "__main__":
    main()
