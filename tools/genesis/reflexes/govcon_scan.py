# CUI // SP-CTI
"""GovCon Scan Reflex — daily SAM.gov incremental scan + demand signal
extraction + Pulse bridge.

Flow:
1. Run incremental SAM.gov scan (only new/updated since last sync)
2. Extract pain points from new opportunities
3. Run demand signal detection
4. Bridge high-demand signals to Pulse for article generation
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the GovCon Scan Reflex."""
    results = {
        "sam_scan": None,
        "demand_signals": None,
        "pulse_bridge": None,
    }
    errors = 0

    # Step 1: Incremental SAM.gov scan
    try:
        from tools.govcon.sam_scanner import scan_sam_gov

        scan = scan_sam_gov()
        new = scan.get("new_count", 0)
        updated = scan.get("updated_count", 0)
        skipped = scan.get("skipped_count", 0)
        scan_errors = len(scan.get("errors", []))
        results["sam_scan"] = {
            "new": new,
            "updated": updated,
            "skipped": skipped,
            "errors": scan_errors,
        }
        print(f"  GovCon: SAM.gov scan — {new} new, {updated} updated, {skipped} skipped, {scan_errors} errors")
        if scan.get("error"):
            print(f"  GovCon: SAM.gov error — {scan['error']}")
    except Exception as e:
        errors += 1
        print(f"  GovCon: SAM.gov scan failed — {e}")
        results["sam_scan"] = {"error": str(e)}

    # Step 2: Extract demand signals from opportunities
    try:
        from tools.pulse.engine.demand_detector import (
            aggregate_demand_signals,
            get_high_demand_signals,
        )

        # Aggregate from all sources (SAM.gov, existing pain points)
        agg = aggregate_demand_signals()
        high = get_high_demand_signals()
        new_high = [s for s in high if not s.get("article_generated")]
        results["demand_signals"] = {
            "total": len(agg) if agg else 0,
            "high_demand": len(high) if high else 0,
            "pending_articles": len(new_high),
        }
        print(f"  GovCon: Demand signals — {len(high) if high else 0} high demand, {len(new_high)} pending articles")
    except Exception as e:
        errors += 1
        print(f"  GovCon: Demand detection failed — {e}")
        results["demand_signals"] = {"error": str(e)}

    # Step 3: Bridge high-demand signals to Pulse for article creation
    pending = results.get("demand_signals", {}).get("pending_articles", 0)
    if pending > 0 and new_high:
        articles_created = 0
        for signal in new_high[:3]:  # Max 3 articles per cycle
            topic = signal.get("pain_point_text", "")
            keywords = signal.get("keywords", "")
            if not topic:
                continue
            try:
                from tools.pulse.scheduler import run_pipeline_from_draft

                article_topic = f"How ICDEV™ Solves: {topic[:80]}" if not topic.startswith("How") else topic
                body = (
                    f"Federal agencies face a critical challenge: {topic}. "
                    f"Keywords: {keywords}. "
                    f"This article explores how modern AI-driven certified "
                    f"development frameworks address this gap with automation, "
                    f"continuous compliance, and deterministic tooling."
                )
                run_pipeline_from_draft(article_topic, body)
                articles_created += 1
                print(f"  GovCon: Pulse article created — {article_topic[:50]}")

                # Mark signal as article_generated
                try:
                    from tools.db.storage import get_connection

                    conn = get_connection()
                    conn.execute(
                        "UPDATE pulse_demand_signals SET article_generated = 1 WHERE id = %s",
                        (signal.get("id"),),
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass
            except Exception as e:
                print(f"  GovCon: Pulse article failed — {e}")

        results["pulse_bridge"] = {"articles_created": articles_created}

        # Telegram notification
        try:
            from tools.notifications.adapters.telegram import send

            send(
                "GovCon → Pulse Bridge",
                f"{articles_created} article(s) created from demand signals. {pending - articles_created} remaining.",
                severity="success",
            )
        except Exception:
            pass

    total_new = results.get("sam_scan", {}).get("new", 0)
    return {
        "success": errors == 0,
        "metric_value": total_new,
        "details": results,
    }