"""Pytest configuration for the AIWorks test suite."""

import os

# Disable DeepEval PostHog telemetry during test runs (CI-friendly, fewer network calls).
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
