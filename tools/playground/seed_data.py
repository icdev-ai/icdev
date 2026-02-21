#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the ICDEV Playground database with sample data."""
import sqlite3
import uuid
from datetime import datetime, timezone


def seed_playground_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            compliance_score INTEGER DEFAULT 0,
            classification TEXT DEFAULT 'CUI',
            impact_level TEXT DEFAULT 'IL4',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS nist_controls (
            id TEXT PRIMARY KEY,
            control_id TEXT NOT NULL,
            project_id TEXT,
            title TEXT,
            status TEXT DEFAULT 'not_assessed',
            implementation_status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS crosswalk_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_control TEXT NOT NULL,
            target_framework TEXT NOT NULL,
            target_requirement TEXT NOT NULL,
            status TEXT DEFAULT 'inherited'
        );
        CREATE TABLE IF NOT EXISTS cmmc_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT DEFAULT 'proj-demo-001',
            domain TEXT NOT NULL,
            total INTEGER DEFAULT 0,
            met INTEGER DEFAULT 0,
            partial INTEGER DEFAULT 0,
            not_met INTEGER DEFAULT 0,
            score REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS fedramp_families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT DEFAULT 'proj-demo-001',
            family TEXT NOT NULL,
            total INTEGER DEFAULT 0,
            satisfied INTEGER DEFAULT 0,
            partial INTEGER DEFAULT 0,
            not_satisfied INTEGER DEFAULT 0,
            score REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS stig_findings (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            rule_id TEXT,
            severity TEXT,
            status TEXT DEFAULT 'open',
            title TEXT
        );
        CREATE TABLE IF NOT EXISTS poam_items (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            finding TEXT,
            status TEXT DEFAULT 'open',
            severity TEXT,
            milestone TEXT,
            due_date TEXT
        );
    """)

    # 3 sample projects
    projects = [
        ("proj-demo-001", "Mission Planning System", "active", 87, "CUI", "IL5"),
        ("proj-demo-002", "Logistics Dashboard", "active", 72, "CUI", "IL4"),
        ("proj-demo-003", "Training Portal", "planning", 45, "CUI", "IL4"),
    ]
    for p in projects:
        conn.execute(
            "INSERT OR IGNORE INTO projects (id, name, status, compliance_score, classification, impact_level) VALUES (?, ?, ?, ?, ?, ?)", p
        )

    # 20 NIST controls for proj-demo-001
    nist_controls = [
        ("AC-2", "Account Management", "satisfied", "implemented"),
        ("AC-3", "Access Enforcement", "satisfied", "implemented"),
        ("AC-6", "Least Privilege", "partially_satisfied", "partial"),
        ("AU-2", "Audit Events", "satisfied", "implemented"),
        ("AU-3", "Content of Audit Records", "satisfied", "implemented"),
        ("AU-6", "Audit Review, Analysis, Reporting", "partially_satisfied", "partial"),
        ("CA-7", "Continuous Monitoring", "not_satisfied", "planned"),
        ("CM-2", "Baseline Configuration", "satisfied", "implemented"),
        ("CM-6", "Configuration Settings", "satisfied", "implemented"),
        ("IA-2", "Identification and Authentication", "satisfied", "implemented"),
        ("IA-5", "Authenticator Management", "partially_satisfied", "partial"),
        ("IR-4", "Incident Handling", "not_satisfied", "planned"),
        ("MA-5", "Maintenance Personnel", "satisfied", "implemented"),
        ("PE-3", "Physical Access Control", "satisfied", "implemented"),
        ("RA-5", "Vulnerability Scanning", "satisfied", "implemented"),
        ("SC-7", "Boundary Protection", "satisfied", "implemented"),
        ("SC-8", "Transmission Confidentiality", "partially_satisfied", "partial"),
        ("SC-13", "Cryptographic Protection", "satisfied", "implemented"),
        ("SI-2", "Flaw Remediation", "partially_satisfied", "partial"),
        ("SI-4", "Information System Monitoring", "satisfied", "implemented"),
    ]
    for i, (cid, title, status, impl) in enumerate(nist_controls):
        conn.execute(
            "INSERT OR IGNORE INTO nist_controls (id, control_id, project_id, title, status, implementation_status) VALUES (?, ?, ?, ?, ?, ?)",
            (f"ctrl-demo-{i:03d}", cid, "proj-demo-001", title, status, impl),
        )

    # Crosswalk mappings from NIST to FedRAMP/CMMC/800-171
    crosswalk = [
        ("AC-2", "FedRAMP", "AC-2", "satisfied"),
        ("AC-2", "CMMC", "AC.L2-3.1.1", "met"),
        ("AC-2", "NIST 800-171", "3.1.1", "satisfied"),
        ("AC-3", "FedRAMP", "AC-3", "satisfied"),
        ("AC-3", "CMMC", "AC.L2-3.1.2", "met"),
        ("SC-7", "FedRAMP", "SC-7", "satisfied"),
        ("SC-7", "CMMC", "SC.L2-3.13.1", "met"),
        ("SC-7", "CJIS", "5.10.1.1", "inherited"),
        ("IA-2", "FedRAMP", "IA-2", "satisfied"),
        ("IA-2", "CMMC", "IA.L2-3.5.1", "met"),
        ("IA-2", "HIPAA", "164.312(d)", "inherited"),
        ("AU-2", "FedRAMP", "AU-2", "satisfied"),
        ("AU-2", "CMMC", "AU.L2-3.3.1", "met"),
        ("SI-4", "FedRAMP", "SI-4", "satisfied"),
        ("SI-4", "CMMC", "SI.L2-3.14.6", "met"),
    ]
    for src, fw, tgt, st in crosswalk:
        conn.execute(
            "INSERT INTO crosswalk_mappings (source_control, target_framework, target_requirement, status) VALUES (?, ?, ?, ?)",
            (src, fw, tgt, st),
        )

    # CMMC domain scores
    cmmc_domains = [
        ("AC - Access Control", 22, 18, 3, 1, 86.4),
        ("AT - Awareness & Training", 3, 3, 0, 0, 100.0),
        ("AU - Audit & Accountability", 9, 7, 2, 0, 88.9),
        ("CA - Assessment & Authorization", 4, 2, 1, 1, 62.5),
        ("CM - Configuration Management", 9, 8, 1, 0, 94.4),
        ("IA - Identification & Authentication", 11, 9, 2, 0, 90.9),
        ("IR - Incident Response", 3, 1, 1, 1, 50.0),
        ("MA - Maintenance", 6, 5, 1, 0, 91.7),
        ("MP - Media Protection", 8, 7, 1, 0, 93.8),
        ("PE - Physical Protection", 6, 6, 0, 0, 100.0),
        ("PS - Personnel Security", 2, 2, 0, 0, 100.0),
        ("RA - Risk Assessment", 3, 2, 1, 0, 83.3),
        ("SC - System & Comm Protection", 16, 13, 2, 1, 87.5),
        ("SI - System & Info Integrity", 7, 5, 2, 0, 85.7),
    ]
    for domain, total, met, partial, not_met, score in cmmc_domains:
        conn.execute(
            "INSERT INTO cmmc_domains (domain, total, met, partial, not_met, score) VALUES (?, ?, ?, ?, ?, ?)",
            (domain, total, met, partial, not_met, score),
        )

    # FedRAMP family scores
    fedramp_families = [
        ("AC - Access Control", 25, 20, 4, 1, 88.0),
        ("AU - Audit and Accountability", 12, 10, 2, 0, 91.7),
        ("CA - Assessment", 9, 6, 2, 1, 77.8),
        ("CM - Configuration Management", 11, 10, 1, 0, 95.5),
        ("IA - Identification", 11, 9, 2, 0, 90.9),
        ("IR - Incident Response", 8, 5, 2, 1, 75.0),
        ("SC - System Protection", 24, 20, 3, 1, 89.6),
        ("SI - System Integrity", 12, 9, 2, 1, 83.3),
    ]
    for family, total, sat, partial, not_sat, score in fedramp_families:
        conn.execute(
            "INSERT INTO fedramp_families (family, total, satisfied, partial, not_satisfied, score) VALUES (?, ?, ?, ?, ?, ?)",
            (family, total, sat, partial, not_sat, score),
        )

    # STIG findings
    stigs = [
        ("stig-d001", "proj-demo-001", "SV-230221", "high", "open", "Ensure root login is disabled"),
        ("stig-d002", "proj-demo-001", "SV-230222", "medium", "open", "Set password complexity"),
        ("stig-d003", "proj-demo-001", "SV-230223", "medium", "remediated", "Enable audit logging"),
        ("stig-d004", "proj-demo-001", "SV-230224", "low", "open", "Set login banner text"),
    ]
    for s in stigs:
        conn.execute(
            "INSERT OR IGNORE INTO stig_findings (id, project_id, rule_id, severity, status, title) VALUES (?, ?, ?, ?, ?, ?)", s
        )

    # POAM items
    poams = [
        ("poam-d001", "proj-demo-001", "Continuous monitoring not fully implemented", "open", "high", "Deploy monitoring agents", "2026-04-15"),
        ("poam-d002", "proj-demo-001", "Incident response plan incomplete", "open", "medium", "Complete IR tabletop exercise", "2026-05-01"),
        ("poam-d003", "proj-demo-001", "Transmission confidentiality partial", "open", "medium", "Implement TLS 1.3 everywhere", "2026-03-30"),
    ]
    for pm in poams:
        conn.execute(
            "INSERT OR IGNORE INTO poam_items (id, project_id, finding, status, severity, milestone, due_date) VALUES (?, ?, ?, ?, ?, ?, ?)", pm
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "playground.db"
    seed_playground_db(path)
    print(f"Seeded playground DB at {path}")
