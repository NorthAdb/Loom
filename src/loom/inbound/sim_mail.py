"""The simulated mail channel: fixture files standing in for a real IMAP inbox.

Gate 7's exit bar is simulated inbound (ADR-015); real IMAP triage is Gate 8
and stays out of this tree. A fixture is one JSON file per message; the
`authenticated` field is the seam's simulated authentication verdict (the
operator's own address), NOT anything the message claims about itself.
"""

import json
from pathlib import Path

from loom.inbound.types import InboundMessage

_REQUIRED_FIELDS = ("message_id", "channel", "from_header", "subject", "body", "received_at", "authenticated")


def load_sim_mail(fixtures_dir: Path) -> "list[InboundMessage]":
    """Parse every *.json fixture into an InboundMessage, filename order."""
    messages: list[InboundMessage] = []
    for path in sorted(Path(fixtures_dir).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        missing = [field for field in _REQUIRED_FIELDS if field not in payload]
        if missing:
            raise ValueError(f"fixture {path.name} is missing fields: {', '.join(missing)}")
        messages.append(
            InboundMessage(
                message_id=str(payload["message_id"]),
                channel=str(payload["channel"]),
                from_header=str(payload["from_header"]),
                subject=str(payload["subject"]),
                body=str(payload["body"]),
                received_at=str(payload["received_at"]),
                authenticated=bool(payload["authenticated"]),
            )
        )
    return messages
