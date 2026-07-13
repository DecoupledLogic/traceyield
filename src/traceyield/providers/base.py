#!/usr/bin/env python3
"""The provider protocol (E3-F2-S4): the contract every usage-log provider
must satisfy for `canonical.ingest()` to consume it.

Design note: `typing.Protocol` (`@runtime_checkable`), not `abc.ABC`. The
producer/consumer split documented in canonical.py's module docstring --
"Adding a provider = adding one Provider class. Nothing else changes." --
only holds if a new provider can be *any* object shaped the right way, with
zero required coupling to this package. An `abc.ABC` base class would force
every provider (including a third-party or test-only one) to import this
module and subclass it just to participate; a structural `Protocol` lets
`ingest()` (and this module's own `isinstance()` checks, via
`@runtime_checkable`) verify the shape without that import, which is exactly
what AC2 ("adding a provider requires no reporting-layer edits") needs: a new
provider only has to *look* like a Provider, not *be* one by inheritance.
`ClaudeProvider` / `CodexProvider` (traceyield.providers.claude / .codex)
satisfy this protocol without importing it.

The contract matches EXACTLY what canonical.ingest() calls on a provider --
read that function before changing this file:

    for prov in providers:
        for root in prov.roots():
            for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
                for rec in prov.parse_file(f):
                    write(conn, rec, verbatim)

...plus `prov.name`, used by `default_providers()` callers/tests (e.g.
`{p.name for p in canonical.default_providers()}`) to identify a provider.
Nothing here is invented beyond those three names.
"""
from typing import Iterator, List, Protocol, Union, runtime_checkable

from traceyield.models import RawEvent, Segment, Session, ToolCall, Turn

# The union of every neutral record type a provider may yield -- the
# "Rec" the canonical-data-model doc and canonical.py's write() refer to.
Rec = Union[Session, Turn, ToolCall, Segment, RawEvent]


@runtime_checkable
class Provider(Protocol):
    """Structural contract: an object with a `name`, a `roots()` method
    returning glob roots to scan, and a `parse_file(path)` method that
    yields neutral Recs from one file. See module docstring."""

    name: str

    def roots(self) -> List[str]:
        """Directories `ingest()` should glob `**/*.jsonl` under."""
        ...

    def parse_file(self, path: str) -> Iterator[Rec]:
        """Parse one file, yielding neutral Session/Turn/ToolCall/Segment/
        RawEvent records in any order (canonical.write() dispatches on
        type(rec), so callers never need to sort or batch by kind)."""
        ...
