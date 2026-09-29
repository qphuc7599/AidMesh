"""Experiment orchestration for development, tuning, confirmation, and stress."""

from .allocation import generate_problem, run_allocation_matrix, write_allocation_results
from .crypto_benchmark import run_reference_crypto_benchmark, write_crypto_benchmark

__all__ = [
    "generate_problem",
    "run_allocation_matrix",
    "run_reference_crypto_benchmark",
    "write_allocation_results",
    "write_crypto_benchmark",
]
