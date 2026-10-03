"""Scam and fraud site detection for pages the agent's browser is on."""

from .classifier import PAGE_JS, JevSiteClassifier, Page, SiteCheckError, Verdict

__all__ = ["PAGE_JS", "JevSiteClassifier", "Page", "SiteCheckError", "Verdict"]
