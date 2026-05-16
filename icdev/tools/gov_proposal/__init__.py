# CUI // SP-PROPIN
"""icdev.tools.gov_proposal — GovProposal subsystem integrated into ICDEV core.

Enabled when ICDEV_GOV_PROPOSAL_ENABLED=true in .env.
"""
from .routes import gov_proposal_bp

__all__ = ["gov_proposal_bp"]
