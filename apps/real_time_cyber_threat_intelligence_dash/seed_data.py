"""Auto-generated seed data — AppForge."""
import random
import uuid
from models import get_connection, init_db


def run_seed():
    result = init_db()
    print(f"DB initialized: {result}")
    conn = get_connection()
    try:
        # Seed Threat
        for i in range(30):
            conn.execute(
                "INSERT OR IGNORE INTO threats (id, name, description, lat, lon, score) VALUES (?,?,?,?,?,?)",
                (f"threat_{uuid.uuid4().hex[:8]}", f"Threat-{i+1:03d}",
                 "Auto-generated threat entity", random.uniform(-40, 60),
                 random.uniform(-120, 150), random.uniform(0.3, 0.95))
            )
        # Seed Vulnerability
        for i in range(30):
            conn.execute(
                "INSERT OR IGNORE INTO vulnerabilitys (id, name, description, lat, lon, score) VALUES (?,?,?,?,?,?)",
                (f"vulnerability_{uuid.uuid4().hex[:8]}", f"Vulnerability-{i+1:03d}",
                 "Auto-generated vulnerability entity", random.uniform(-40, 60),
                 random.uniform(-120, 150), random.uniform(0.3, 0.95))
            )
        # Seed Asset
        for i in range(30):
            conn.execute(
                "INSERT OR IGNORE INTO assets (id, name, description, lat, lon, score) VALUES (?,?,?,?,?,?)",
                (f"asset_{uuid.uuid4().hex[:8]}", f"Asset-{i+1:03d}",
                 "Auto-generated asset entity", random.uniform(-40, 60),
                 random.uniform(-120, 150), random.uniform(0.3, 0.95))
            )
        # Seed Indicator
        for i in range(30):
            conn.execute(
                "INSERT OR IGNORE INTO indicators (id, name, description, lat, lon, score) VALUES (?,?,?,?,?,?)",
                (f"indicator_{uuid.uuid4().hex[:8]}", f"Indicator-{i+1:03d}",
                 "Auto-generated indicator entity", random.uniform(-40, 60),
                 random.uniform(-120, 150), random.uniform(0.3, 0.95))
            )
        # Seed Campaign
        for i in range(30):
            conn.execute(
                "INSERT OR IGNORE INTO campaigns (id, name, description, lat, lon, score) VALUES (?,?,?,?,?,?)",
                (f"campaign_{uuid.uuid4().hex[:8]}", f"Campaign-{i+1:03d}",
                 "Auto-generated campaign entity", random.uniform(-40, 60),
                 random.uniform(-120, 150), random.uniform(0.3, 0.95))
            )
        # Seed Actor
        for i in range(30):
            conn.execute(
                "INSERT OR IGNORE INTO actors (id, name, description, lat, lon, score) VALUES (?,?,?,?,?,?)",
                (f"actor_{uuid.uuid4().hex[:8]}", f"Actor-{i+1:03d}",
                 "Auto-generated actor entity", random.uniform(-40, 60),
                 random.uniform(-120, 150), random.uniform(0.3, 0.95))
            )

        # Seed KG edges
        sources = [r[0] for r in conn.execute("SELECT id FROM threats LIMIT 20").fetchall()]
        targets = [r[0] for r in conn.execute("SELECT id FROM vulnerabilitys LIMIT 20").fetchall()]
        for rel in ["targets", "exploits", "mitigates", "attributed_to", "indicates"]:
            for s in sources:
                for t in random.sample(targets, min(3, len(targets))):
                    conn.execute(
                        "INSERT INTO kg_edges (source_type, source_id, target_type, target_id, relation, weight) "
                        "VALUES (?,?,?,?,?,?)",
                        ("threat", s, "vulnerability", t, rel, random.uniform(0.3, 1.0))
                    )

        conn.commit()
        print("Seed complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    run_seed()
