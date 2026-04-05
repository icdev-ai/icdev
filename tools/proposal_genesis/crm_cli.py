#!/usr/bin/env python3
# CUI // SP-CTI
"""CRM CLI — Manual account, contact, and interaction management.

Usage:
    # Accounts
    python tools/proposal_genesis/crm_cli.py --list-accounts --json
    python tools/proposal_genesis/crm_cli.py --get-account pgacct-xxx --json
    python tools/proposal_genesis/crm_cli.py --create-account --name "DISA" --agency "DISA" --account-type government --json
    python tools/proposal_genesis/crm_cli.py --update-account pgacct-xxx --status inactive --json

    # Contacts
    python tools/proposal_genesis/crm_cli.py --list-contacts --json
    python tools/proposal_genesis/crm_cli.py --list-contacts --account-id pgacct-xxx --json
    python tools/proposal_genesis/crm_cli.py --get-contact pgcon-xxx --json
    python tools/proposal_genesis/crm_cli.py --create-contact --account-id pgacct-xxx --name "Jane Smith" --title "COR" --email "jane@agency.mil" --influence-level decision_maker --json
    python tools/proposal_genesis/crm_cli.py --update-contact pgcon-xxx --title "CO" --json
    python tools/proposal_genesis/crm_cli.py --delete-contact pgcon-xxx --json

    # Interactions
    python tools/proposal_genesis/crm_cli.py --list-interactions --json
    python tools/proposal_genesis/crm_cli.py --log-interaction --account-id pgacct-xxx --type meeting --subject "Kickoff call" --json
    python tools/proposal_genesis/crm_cli.py --log-interaction --account-id pgacct-xxx --contact-id pgcon-xxx --type call --subject "Follow-up" --notes "Discussed timeline" --json
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.engage import (  # noqa: E402
    create_account,
    create_contact,
    delete_contact,
    get_account,
    get_contact,
    list_accounts,
    list_contacts,
    log_manual_interaction,
    update_account,
    update_contact,
)


def _print(data):
    print(json.dumps(data, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description="CRM CLI — Manual CRM management")

    # Output format
    parser.add_argument("--json", action="store_true", help="JSON output")

    # Account operations
    parser.add_argument("--list-accounts", action="store_true")
    parser.add_argument("--get-account", metavar="ID")
    parser.add_argument("--create-account", action="store_true")
    parser.add_argument("--update-account", metavar="ID")

    # Contact operations
    parser.add_argument("--list-contacts", action="store_true")
    parser.add_argument("--get-contact", metavar="ID")
    parser.add_argument("--create-contact", action="store_true")
    parser.add_argument("--update-contact", metavar="ID")
    parser.add_argument("--delete-contact", metavar="ID")

    # Interaction operations
    parser.add_argument("--list-interactions", action="store_true")
    parser.add_argument("--log-interaction", action="store_true")

    # Shared fields
    parser.add_argument("--account-id", help="Account ID")
    parser.add_argument("--contact-id", help="Contact ID")
    parser.add_argument("--name", help="Name (account or contact)")
    parser.add_argument("--agency", default="")
    parser.add_argument("--sub-agency", default="")
    parser.add_argument(
        "--account-type", default="government", choices=["government", "prime", "subcontractor", "partner", "other"]
    )
    parser.add_argument("--website", default="")
    parser.add_argument("--naics-codes", default="")
    parser.add_argument("--set-asides", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--status", default="active", choices=["active", "inactive", "prospect"])
    parser.add_argument("--title", default="", help="Contact title/role")
    parser.add_argument("--email", default="")
    parser.add_argument("--phone", default="")
    parser.add_argument("--role-in-procurement", default="")
    parser.add_argument(
        "--influence-level",
        default="unknown",
        choices=["decision_maker", "influencer", "evaluator", "end_user", "unknown"],
    )
    parser.add_argument(
        "--type",
        dest="interaction_type",
        default="meeting",
        choices=["meeting", "call", "email", "conference", "site_visit", "rfi_response", "industry_day", "other"],
    )
    parser.add_argument("--subject", default="")
    parser.add_argument("--opportunity-id", default="")
    parser.add_argument("--interaction-date", default=None)
    parser.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()

    # ── Account operations ──────────────────────────────────────────────────
    if args.list_accounts:
        accounts = list_accounts(limit=args.limit)
        _print({"accounts": accounts, "count": len(accounts)})

    elif args.get_account:
        acct = get_account(args.get_account)
        _print({"account": acct} if acct else {"error": "not found"})

    elif args.create_account:
        if not args.name:
            _print({"error": "--name is required for --create-account"})
            sys.exit(1)
        result = create_account(
            name=args.name,
            agency=args.agency,
            sub_agency=args.sub_agency,
            account_type=args.account_type,
            website=args.website,
            naics_codes=args.naics_codes,
            set_asides=args.set_asides,
            notes=args.notes,
            status=args.status,
        )
        _print(result)

    elif args.update_account:
        fields = {}
        if args.name:
            fields["name"] = args.name
        if args.agency:
            fields["agency"] = args.agency
        if args.sub_agency:
            fields["sub_agency"] = args.sub_agency
        if args.account_type != "government":
            fields["account_type"] = args.account_type
        if args.website:
            fields["website"] = args.website
        if args.naics_codes:
            fields["naics_codes"] = args.naics_codes
        if args.set_asides:
            fields["set_asides"] = args.set_asides
        if args.notes:
            fields["notes"] = args.notes
        if args.status != "active":
            fields["status"] = args.status
        result = update_account(args.update_account, **fields)
        _print(result)

    # ── Contact operations ──────────────────────────────────────────────────
    elif args.list_contacts:
        contacts = list_contacts(account_id=args.account_id, limit=args.limit)
        _print({"contacts": contacts, "count": len(contacts)})

    elif args.get_contact:
        contact = get_contact(args.get_contact)
        _print({"contact": contact} if contact else {"error": "not found"})

    elif args.create_contact:
        if not args.account_id:
            _print({"error": "--account-id is required for --create-contact"})
            sys.exit(1)
        if not args.name:
            _print({"error": "--name is required for --create-contact"})
            sys.exit(1)
        result = create_contact(
            account_id=args.account_id,
            name=args.name,
            title=args.title,
            email=args.email,
            phone=args.phone,
            role_in_procurement=args.role_in_procurement,
            influence_level=args.influence_level,
            notes=args.notes,
        )
        _print(result)

    elif args.update_contact:
        fields = {}
        if args.name:
            fields["name"] = args.name
        if args.title:
            fields["title"] = args.title
        if args.email:
            fields["email"] = args.email
        if args.phone:
            fields["phone"] = args.phone
        if args.role_in_procurement:
            fields["role_in_procurement"] = args.role_in_procurement
        if args.influence_level != "unknown":
            fields["influence_level"] = args.influence_level
        if args.notes:
            fields["notes"] = args.notes
        if args.account_id:
            fields["account_id"] = args.account_id
        result = update_contact(args.update_contact, **fields)
        _print(result)

    elif args.delete_contact:
        result = delete_contact(args.delete_contact)
        _print(result)

    # ── Interaction operations ──────────────────────────────────────────────
    elif args.list_interactions:
        from tools.proposal_genesis.reflexes.engage import get_connection

        conn = get_connection()
        try:
            query = (
                "SELECT ci.*, ca.name AS account_name, "
                "cc.name AS contact_name "
                "FROM pg_crm_interactions ci "
                "LEFT JOIN pg_crm_accounts ca ON ca.id = ci.account_id "
                "LEFT JOIN pg_crm_contacts cc ON cc.id = ci.contact_id "
            )
            params: list = []
            if args.account_id:
                query += "WHERE ci.account_id = ? "
                params.append(args.account_id)
            query += "ORDER BY ci.interaction_date DESC LIMIT ?"
            params.append(args.limit)
            rows = conn.execute(query, params).fetchall()
            interactions = [dict(r) for r in rows]
            _print({"interactions": interactions, "count": len(interactions)})
        except Exception as exc:
            _print({"interactions": [], "count": 0, "error": str(exc)})
        finally:
            conn.close()

    elif args.log_interaction:
        if not args.account_id:
            _print({"error": "--account-id is required for --log-interaction"})
            sys.exit(1)
        if not args.subject:
            _print({"error": "--subject is required for --log-interaction"})
            sys.exit(1)
        result = log_manual_interaction(
            account_id=args.account_id,
            interaction_type=args.interaction_type,
            subject=args.subject,
            contact_id=args.contact_id or "",
            notes=args.notes,
            opportunity_id=args.opportunity_id,
            interaction_date=args.interaction_date,
        )
        _print(result)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
