"""Frozen-style validation, aggregation, statistics, and paper assets."""

from .pipeline import aggregate, load_run_summaries, paired_differences, write_assets

__all__ = ["aggregate", "load_run_summaries", "paired_differences", "write_assets"]
