# CUI // SP-CTI
"""Federated Data Mesh for Conflict Intelligence.

Orchestrates multi-provider data ingestion, ETL normalization, and
ML-driven conflict escalation prediction.

Components:
  - providers/  : MeshProvider adapters (ACLED, GDELT, ReliefWeb)
  - mesh_coordinator.py : federated pull + deduplication
  - etl_pipeline.py     : normalize → sg_conflict_events
  - ml_pattern_engine.py: NLP signal extraction
  - escalation_predictor.py: risk scoring + conflict_predictions store
"""
