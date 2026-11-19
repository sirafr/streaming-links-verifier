"""Verify Amazon Prime Video availability alerts against the live detail pages."""

from .checker import PlatformChecker, PrimeVideoChecker
from .models import CheckResult, InputRow, Offer, Verdict

__all__ = ["CheckResult", "InputRow", "Offer", "PlatformChecker", "PrimeVideoChecker", "Verdict"]
__version__ = "0.1.0"
