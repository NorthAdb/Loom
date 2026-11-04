"""Types for the untrusted inbound seam.

An `InboundMessage` is data, never instructions and never an identity. The
`authenticated` flag is the seam's own verdict — in the simulated channel it
is the fixture's declaration, in a real channel it would be the transport's
authentication (SPF+DKIM+DMARC, a webhook signature). The message body never
sets it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class InboundMessage:
    """One untrusted inbound item awaiting triage."""

    message_id: str
    channel: str
    from_header: str
    subject: str
    body: str
    received_at: str
    authenticated: bool
