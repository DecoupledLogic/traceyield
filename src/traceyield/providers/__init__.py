#!/usr/bin/env python3
"""The provider layer (E3-F2-S4): each coding-assistant provider gets its
own module here, depending only on the neutral layer (models/paths/pricing/
classification/transcripts) -- never on canonical.py (the store/consumer) or
report.py (reporting). See traceyield.providers.base.Provider for the
protocol, traceyield.providers.claude/.codex for the two shipped providers.

Adding a new provider: write a module in this package (or anywhere) whose
class has a `name`, a `roots()` method, and a `parse_file(path)` method that
yields traceyield.models Recs -- see base.Provider's docstring and
tests/test_providers.py's FakeProvider for a minimal example that needs no
import from this package at all (Provider is a structural typing.Protocol,
not a base class to subclass). Register it by passing it in the `providers=`
list to canonical.ingest(), or add it to canonical.default_providers() if it
should run by default. No change to report.py (or anywhere in the reporting
layer) is required either way -- that's the whole point.
"""
from traceyield.providers.base import Provider
from traceyield.providers.claude import ClaudeProvider
from traceyield.providers.codex import CODEX_TIER, CodexProvider, codex_tier

__all__ = ["Provider", "ClaudeProvider", "CodexProvider", "codex_tier", "CODEX_TIER"]
