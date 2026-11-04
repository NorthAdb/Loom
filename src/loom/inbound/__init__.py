"""Inbound: the untrusted seam where outside messages become tracked work (Gate 7).

`types` defines the message (data, never instructions, never identity);
`accept` is the one step that records the message, creates the triage task,
and issues the minimal scope; `sim_mail` is the fixture-driven channel that
stands in for real mail until Gate 8; `store` keeps the inbox the console
reads.
"""

from loom.inbound.accept import accept_inbound, compose_triage_prompt, fail_triage
from loom.inbound.sim_mail import load_sim_mail
from loom.inbound.store import InboxRecord, InboxStore
from loom.inbound.types import InboundMessage

__all__ = [
    "InboundMessage",
    "InboxRecord",
    "InboxStore",
    "accept_inbound",
    "compose_triage_prompt",
    "fail_triage",
    "load_sim_mail",
]
