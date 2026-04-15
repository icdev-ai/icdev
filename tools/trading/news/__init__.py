"""
tools.trading.news — News ingestion, classification, and scenario-matching pipeline.

Implements the AlphaDesk /news feature: RSS ingestion → classifier → scenario matcher
→ probabilistic impact display, reusing the existing scenario engine and INTaaS
perspective scorer with no new projection math.

See implementation plan: ~/.claude/plans/recursive-noodling-meteor.md
"""

__version__ = '0.1.0'
