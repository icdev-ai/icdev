"""Tests for Federal Network Peering — IP Address Space & Routing Policy (step 3).

Workflow: processify-wfl-f77f / step 3
"""
import sqlite3
import uuid

import pytest

from tools.network.ip_address_space import (
    CLASSIFICATION,
    STEP_NUMBER,
    WORKFLOW_ID,
    _default_routing_policy,
    _init_schema,
    acknowledge_definition,
    add_prefix,
    approve_definition,
    create_ip_space_definition,
    generate_definition_document,
    get_ip_space_definition,
    list_ip_space_definitions,
    reject_definition,
    remove_prefix,
    set_routing_policy,
    submit_definition,
    validate_prefix,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _init_schema(c)
    yield c
    c.close()


@pytest.fixture
def draft(conn):
    """A draft definition with no prefixes."""
    return create_ip_space_definition(
        conn,
        initiating_party_name="Agency Alpha",
        responding_party_name="Agency Beta",
    )


@pytest.fixture
def draft_with_prefixes(conn, draft):
    """Draft definition with one prefix per party."""
    add_prefix(conn, draft["definition_id"], "192.0.2.0/24",
               party_role="initiating", prefix_type="aggregate",
               description="Alpha aggregate")
    add_prefix(conn, draft["definition_id"], "198.51.100.0/24",
               party_role="responding", prefix_type="aggregate",
               description="Beta aggregate")
    return get_ip_space_definition(conn, draft["definition_id"])


@pytest.fixture
def submitted(conn, draft_with_prefixes):
    return submit_definition(conn, draft_with_prefixes["definition_id"])


@pytest.fixture
def acknowledged(conn, submitted):
    return acknowledge_definition(conn, submitted["definition_id"])


# ── validate_prefix ───────────────────────────────────────────────────────────

class TestValidatePrefix:
    def test_valid_ipv4(self):
        ok, result = validate_prefix("192.168.1.0/24")
        assert ok
        assert result == "192.168.1.0/24"

    def test_valid_ipv4_host_bit_masked(self):
        ok, result = validate_prefix("192.168.1.5/24")
        assert ok
        assert result == "192.168.1.0/24"  # strict=False normalises

    def test_valid_ipv6(self):
        ok, result = validate_prefix("2001:db8::/32")
        assert ok
        assert "2001:db8::" in result

    def test_invalid_prefix_garbage(self):
        ok, msg = validate_prefix("not-a-prefix")
        assert not ok
        assert "invalid CIDR" in msg

    def test_invalid_prefix_empty(self):
        ok, msg = validate_prefix("")
        assert not ok

    def test_invalid_prefix_none(self):
        ok, msg = validate_prefix(None)
        assert not ok

    def test_host_route(self):
        ok, result = validate_prefix("10.0.0.1/32")
        assert ok
        assert result == "10.0.0.1/32"

    def test_default_route(self):
        ok, result = validate_prefix("0.0.0.0/0")
        assert ok

    def test_ipv6_full(self):
        ok, result = validate_prefix("2001:0db8:0000::/48")
        assert ok


# ── create_ip_space_definition ────────────────────────────────────────────────

class TestCreateDefinition:
    def test_creates_draft(self, conn):
        d = create_ip_space_definition(conn, "Alpha", "Beta")
        assert d["status"] == "draft"
        assert d["workflow_id"] == WORKFLOW_ID
        assert d["step"] == STEP_NUMBER
        assert d["classification"] == CLASSIFICATION

    def test_returns_definition_id_alias(self, conn):
        d = create_ip_space_definition(conn, "Alpha", "Beta")
        assert "definition_id" in d
        assert d["definition_id"] == d["id"]

    def test_default_policy_present(self, conn):
        d = create_ip_space_definition(conn, "Alpha", "Beta")
        policy = d["routing_policy"]
        assert isinstance(policy, dict)
        assert policy["max_prefixes_initiating"] == 200
        assert policy["prefix_filter_action"] == "reject"

    def test_empty_prefixes_list(self, conn):
        d = create_ip_space_definition(conn, "Alpha", "Beta")
        assert d["prefixes"] == []

    def test_with_peering_request_id(self, conn):
        d = create_ip_space_definition(conn, "Alpha", "Beta",
                                       peering_request_id="req-001")
        assert d["peering_request_id"] == "req-001"

    def test_with_asn_exchange_id(self, conn):
        d = create_ip_space_definition(conn, "Alpha", "Beta",
                                       asn_exchange_id="exc-abc")
        assert d["asn_exchange_id"] == "exc-abc"

    def test_with_initial_prefixes(self, conn):
        d = create_ip_space_definition(
            conn, "Alpha", "Beta",
            initial_prefixes=[
                {"prefix": "10.0.0.0/8", "party_role": "initiating",
                 "prefix_type": "aggregate"},
            ],
        )
        assert len(d["prefixes"]) == 1
        assert d["prefixes"][0]["prefix"] == "10.0.0.0/8"

    def test_missing_initiating_name(self, conn):
        with pytest.raises(ValueError, match="initiating_party_name"):
            create_ip_space_definition(conn, "", "Beta")

    def test_missing_responding_name(self, conn):
        with pytest.raises(ValueError, match="responding_party_name"):
            create_ip_space_definition(conn, "Alpha", "")

    def test_initial_prefix_invalid(self, conn):
        with pytest.raises(ValueError):
            create_ip_space_definition(
                conn, "Alpha", "Beta",
                initial_prefixes=[{"prefix": "not-valid", "party_role": "initiating"}],
            )

    def test_party_org_stored(self, conn):
        d = create_ip_space_definition(
            conn, "Alpha", "Beta",
            initiating_party_org="Alpha Inc.",
            responding_party_org="Beta LLC",
        )
        assert d["initiating_party_org"] == "Alpha Inc."
        assert d["responding_party_org"] == "Beta LLC"

    def test_persisted_to_db(self, conn, draft):
        fetched = get_ip_space_definition(conn, draft["definition_id"])
        assert fetched is not None
        assert fetched["definition_id"] == draft["definition_id"]


# ── get / list ────────────────────────────────────────────────────────────────

class TestGetListDefinitions:
    def test_get_returns_none_for_unknown(self, conn):
        assert get_ip_space_definition(conn, str(uuid.uuid4())) is None

    def test_list_empty(self, conn):
        assert list_ip_space_definitions(conn) == []

    def test_list_returns_created(self, conn, draft):
        rows = list_ip_space_definitions(conn)
        assert len(rows) == 1
        assert rows[0]["definition_id"] == draft["definition_id"]

    def test_list_filter_status(self, conn, draft):
        rows = list_ip_space_definitions(conn, status="draft")
        assert len(rows) == 1
        rows_sub = list_ip_space_definitions(conn, status="submitted")
        assert rows_sub == []

    def test_list_filter_workflow(self, conn, draft):
        rows = list_ip_space_definitions(conn, workflow_id=WORKFLOW_ID)
        assert len(rows) == 1

    def test_list_filter_peering_request_id(self, conn):
        create_ip_space_definition(conn, "A", "B", peering_request_id="req-X")
        create_ip_space_definition(conn, "C", "D", peering_request_id="req-Y")
        rows = list_ip_space_definitions(conn, peering_request_id="req-X")
        assert len(rows) == 1
        assert rows[0]["peering_request_id"] == "req-X"

    def test_list_limit(self, conn):
        for i in range(5):
            create_ip_space_definition(conn, f"A{i}", f"B{i}")
        rows = list_ip_space_definitions(conn, limit=3)
        assert len(rows) == 3

    def test_json_fields_decoded(self, conn, draft):
        fetched = get_ip_space_definition(conn, draft["definition_id"])
        assert isinstance(fetched["prefixes"], list)
        assert isinstance(fetched["routing_policy"], dict)


# ── add_prefix ────────────────────────────────────────────────────────────────

class TestAddPrefix:
    def test_add_valid_v4(self, conn, draft):
        result = add_prefix(conn, draft["definition_id"], "10.0.0.0/8",
                            party_role="initiating", prefix_type="aggregate")
        assert len(result["prefixes"]) == 1
        assert result["prefixes"][0]["prefix"] == "10.0.0.0/8"

    def test_add_valid_v6(self, conn, draft):
        result = add_prefix(conn, draft["definition_id"], "2001:db8::/32",
                            party_role="responding", prefix_type="customer")
        assert len(result["prefixes"]) == 1
        assert result["prefixes"][0]["prefix_type"] == "customer"

    def test_add_multiple_prefixes(self, conn, draft):
        add_prefix(conn, draft["definition_id"], "192.0.2.0/24",
                   party_role="initiating")
        add_prefix(conn, draft["definition_id"], "198.51.100.0/24",
                   party_role="responding")
        result = get_ip_space_definition(conn, draft["definition_id"])
        assert len(result["prefixes"]) == 2

    def test_add_customer_prefix_flag(self, conn, draft):
        result = add_prefix(conn, draft["definition_id"], "203.0.113.0/24",
                            party_role="initiating", prefix_type="customer",
                            is_customer_prefix=True)
        assert result["prefixes"][0]["is_customer_prefix"] is True

    def test_add_blackhole_type(self, conn, draft):
        result = add_prefix(conn, draft["definition_id"], "192.0.2.128/25",
                            party_role="initiating", prefix_type="blackhole")
        assert result["prefixes"][0]["prefix_type"] == "blackhole"

    def test_add_transit_type(self, conn, draft):
        result = add_prefix(conn, draft["definition_id"], "192.0.2.0/24",
                            party_role="responding", prefix_type="transit")
        assert result["prefixes"][0]["prefix_type"] == "transit"

    def test_invalid_prefix_rejected(self, conn, draft):
        with pytest.raises(ValueError, match="invalid CIDR|Invalid prefix"):
            add_prefix(conn, draft["definition_id"], "999.0.0.0/8")

    def test_invalid_party_role(self, conn, draft):
        with pytest.raises(ValueError, match="party_role"):
            add_prefix(conn, draft["definition_id"], "192.0.2.0/24",
                       party_role="unknown")

    def test_invalid_prefix_type(self, conn, draft):
        with pytest.raises(ValueError, match="prefix_type"):
            add_prefix(conn, draft["definition_id"], "192.0.2.0/24",
                       prefix_type="bogus")

    def test_cannot_add_to_verified(self, conn, acknowledged):
        approve_definition(conn, acknowledged["definition_id"], "initiating")
        approve_definition(conn, acknowledged["definition_id"], "responding")
        rec = get_ip_space_definition(conn, acknowledged["definition_id"])
        assert rec["status"] == "verified"
        with pytest.raises(ValueError, match="terminal"):
            add_prefix(conn, rec["definition_id"], "10.0.0.0/8")

    def test_cannot_add_to_rejected(self, conn, draft):
        reject_definition(conn, draft["definition_id"])
        with pytest.raises(ValueError, match="terminal"):
            add_prefix(conn, draft["definition_id"], "10.0.0.0/8")

    def test_not_found_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            add_prefix(conn, str(uuid.uuid4()), "10.0.0.0/8")

    def test_description_stored(self, conn, draft):
        result = add_prefix(conn, draft["definition_id"], "10.0.0.0/8",
                            party_role="initiating", description="My block")
        assert result["prefixes"][0]["description"] == "My block"


# ── remove_prefix ─────────────────────────────────────────────────────────────

class TestRemovePrefix:
    def test_remove_by_index(self, conn, draft_with_prefixes):
        did = draft_with_prefixes["definition_id"]
        result = remove_prefix(conn, did, 0)
        assert len(result["prefixes"]) == 1

    def test_remove_out_of_range(self, conn, draft_with_prefixes):
        did = draft_with_prefixes["definition_id"]
        with pytest.raises(ValueError, match="out of range"):
            remove_prefix(conn, did, 99)

    def test_remove_negative_index(self, conn, draft_with_prefixes):
        did = draft_with_prefixes["definition_id"]
        with pytest.raises(ValueError, match="out of range"):
            remove_prefix(conn, did, -1)

    def test_cannot_remove_from_verified(self, conn, acknowledged):
        did = acknowledged["definition_id"]
        approve_definition(conn, did, "initiating")
        approve_definition(conn, did, "responding")
        with pytest.raises(ValueError, match="terminal"):
            remove_prefix(conn, did, 0)

    def test_not_found_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            remove_prefix(conn, str(uuid.uuid4()), 0)


# ── set_routing_policy ────────────────────────────────────────────────────────

class TestSetRoutingPolicy:
    def test_set_max_prefixes(self, conn, draft):
        result = set_routing_policy(conn, draft["definition_id"],
                                    max_prefixes_initiating=50,
                                    max_prefixes_responding=75)
        policy = result["routing_policy"]
        assert policy["max_prefixes_initiating"] == 50
        assert policy["max_prefixes_responding"] == 75

    def test_set_prefix_lengths_v4(self, conn, draft):
        result = set_routing_policy(conn, draft["definition_id"],
                                    min_prefix_length_v4=16,
                                    max_prefix_length_v4=28)
        policy = result["routing_policy"]
        assert policy["min_prefix_length_v4"] == 16
        assert policy["max_prefix_length_v4"] == 28

    def test_set_prefix_lengths_v6(self, conn, draft):
        result = set_routing_policy(conn, draft["definition_id"],
                                    min_prefix_length_v6=32,
                                    max_prefix_length_v6=64)
        policy = result["routing_policy"]
        assert policy["min_prefix_length_v6"] == 32
        assert policy["max_prefix_length_v6"] == 64

    def test_set_communities(self, conn, draft):
        result = set_routing_policy(conn, draft["definition_id"],
                                    accepted_communities=["65000:100", "65000:200"],
                                    rejected_communities=["65535:0"])
        policy = result["routing_policy"]
        assert "65000:100" in policy["accepted_communities"]
        assert "65535:0" in policy["rejected_communities"]

    def test_set_no_export(self, conn, draft):
        result = set_routing_policy(conn, draft["definition_id"], no_export=True)
        assert result["routing_policy"]["no_export"] is True

    def test_set_filter_action_warn(self, conn, draft):
        result = set_routing_policy(conn, draft["definition_id"],
                                    prefix_filter_action="warn")
        assert result["routing_policy"]["prefix_filter_action"] == "warn"

    def test_invalid_filter_action(self, conn, draft):
        with pytest.raises(ValueError, match="prefix_filter_action"):
            set_routing_policy(conn, draft["definition_id"],
                               prefix_filter_action="ignore")

    def test_negative_max_prefixes(self, conn, draft):
        with pytest.raises(ValueError, match="max_prefixes_initiating"):
            set_routing_policy(conn, draft["definition_id"],
                               max_prefixes_initiating=-1)

    def test_set_local_preference_med(self, conn, draft):
        result = set_routing_policy(conn, draft["definition_id"],
                                    local_preference=150, med=10)
        policy = result["routing_policy"]
        assert policy["local_preference"] == 150
        assert policy["med"] == 10

    def test_set_notes(self, conn, draft):
        result = set_routing_policy(conn, draft["definition_id"],
                                    notes="Agreed in telecon 2026-06-27")
        assert "Agreed in telecon" in result["routing_policy"]["notes"]

    def test_partial_update_preserves_other_fields(self, conn, draft):
        set_routing_policy(conn, draft["definition_id"],
                           max_prefixes_initiating=50)
        result = set_routing_policy(conn, draft["definition_id"],
                                    max_prefixes_responding=75)
        policy = result["routing_policy"]
        assert policy["max_prefixes_initiating"] == 50
        assert policy["max_prefixes_responding"] == 75

    def test_cannot_update_verified(self, conn, acknowledged):
        did = acknowledged["definition_id"]
        approve_definition(conn, did, "initiating")
        approve_definition(conn, did, "responding")
        with pytest.raises(ValueError, match="terminal"):
            set_routing_policy(conn, did, max_prefixes_initiating=10)

    def test_not_found_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            set_routing_policy(conn, str(uuid.uuid4()),
                               max_prefixes_initiating=10)


# ── submit_definition ─────────────────────────────────────────────────────────

class TestSubmitDefinition:
    def test_submit_advances_status(self, conn, draft_with_prefixes):
        result = submit_definition(conn, draft_with_prefixes["definition_id"])
        assert result["status"] == "submitted"

    def test_cannot_submit_without_prefixes(self, conn, draft):
        with pytest.raises(ValueError, match="At least one prefix"):
            submit_definition(conn, draft["definition_id"])

    def test_cannot_submit_already_submitted(self, conn, submitted):
        with pytest.raises(ValueError, match="'draft'"):
            submit_definition(conn, submitted["definition_id"])

    def test_not_found_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            submit_definition(conn, str(uuid.uuid4()))


# ── acknowledge_definition ────────────────────────────────────────────────────

class TestAcknowledgeDefinition:
    def test_acknowledge_advances_status(self, conn, submitted):
        result = acknowledge_definition(conn, submitted["definition_id"])
        assert result["status"] == "acknowledged"

    def test_cannot_acknowledge_draft(self, conn, draft_with_prefixes):
        with pytest.raises(ValueError, match="'submitted'"):
            acknowledge_definition(conn, draft_with_prefixes["definition_id"])

    def test_cannot_acknowledge_twice(self, conn, submitted):
        acknowledge_definition(conn, submitted["definition_id"])
        with pytest.raises(ValueError, match="'submitted'"):
            acknowledge_definition(conn, submitted["definition_id"])

    def test_not_found_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            acknowledge_definition(conn, str(uuid.uuid4()))


# ── approve_definition ────────────────────────────────────────────────────────

class TestApproveDefinition:
    def test_single_approval_stays_acknowledged(self, conn, acknowledged):
        result = approve_definition(conn, acknowledged["definition_id"], "initiating")
        assert result["status"] == "acknowledged"
        assert result["initiating_approved"] == 1
        assert result["responding_approved"] == 0

    def test_both_approvals_verify(self, conn, acknowledged):
        did = acknowledged["definition_id"]
        approve_definition(conn, did, "initiating")
        result = approve_definition(conn, did, "responding")
        assert result["status"] == "verified"
        assert result["initiating_approved"] == 1
        assert result["responding_approved"] == 1

    def test_reverse_order_verify(self, conn, acknowledged):
        did = acknowledged["definition_id"]
        approve_definition(conn, did, "responding")
        result = approve_definition(conn, did, "initiating")
        assert result["status"] == "verified"

    def test_notes_appended(self, conn, acknowledged):
        result = approve_definition(conn, acknowledged["definition_id"],
                                    "initiating",
                                    notes="Approved by CISO")
        assert "Approved by CISO" in result["approval_notes"]

    def test_invalid_party_role(self, conn, acknowledged):
        with pytest.raises(ValueError, match="party_role"):
            approve_definition(conn, acknowledged["definition_id"], "unknown")

    def test_cannot_approve_draft(self, conn, draft_with_prefixes):
        with pytest.raises(ValueError, match="'acknowledged'"):
            approve_definition(conn, draft_with_prefixes["definition_id"],
                               "initiating")

    def test_not_found_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            approve_definition(conn, str(uuid.uuid4()), "initiating")


# ── reject_definition ─────────────────────────────────────────────────────────

class TestRejectDefinition:
    def test_reject_draft(self, conn, draft):
        result = reject_definition(conn, draft["definition_id"],
                                   reason="Does not meet prefix requirements")
        assert result["status"] == "rejected"
        assert "prefix requirements" in result["rejection_reason"]

    def test_reject_submitted(self, conn, submitted):
        result = reject_definition(conn, submitted["definition_id"])
        assert result["status"] == "rejected"

    def test_reject_acknowledged(self, conn, acknowledged):
        result = reject_definition(conn, acknowledged["definition_id"])
        assert result["status"] == "rejected"

    def test_cannot_reject_verified(self, conn, acknowledged):
        did = acknowledged["definition_id"]
        approve_definition(conn, did, "initiating")
        approve_definition(conn, did, "responding")
        with pytest.raises(ValueError, match="terminal"):
            reject_definition(conn, did)

    def test_cannot_reject_already_rejected(self, conn, draft):
        reject_definition(conn, draft["definition_id"])
        with pytest.raises(ValueError, match="terminal"):
            reject_definition(conn, draft["definition_id"])

    def test_not_found_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            reject_definition(conn, str(uuid.uuid4()))


# ── generate_definition_document ──────────────────────────────────────────────

class TestGenerateDefinitionDocument:
    def test_document_contains_cui_header(self, conn, draft_with_prefixes):
        doc = generate_definition_document(conn,
                                           draft_with_prefixes["definition_id"])
        assert "CUI // SP-CTI" in doc

    def test_document_contains_party_names(self, conn, draft_with_prefixes):
        doc = generate_definition_document(conn,
                                           draft_with_prefixes["definition_id"])
        assert "Agency Alpha" in doc
        assert "Agency Beta" in doc

    def test_document_contains_prefixes(self, conn, draft_with_prefixes):
        doc = generate_definition_document(conn,
                                           draft_with_prefixes["definition_id"])
        assert "192.0.2.0/24" in doc
        assert "198.51.100.0/24" in doc

    def test_document_contains_routing_policy(self, conn, draft_with_prefixes):
        doc = generate_definition_document(conn,
                                           draft_with_prefixes["definition_id"])
        assert "Max Prefixes" in doc
        assert "Prefix Length" in doc

    def test_document_contains_workflow_id(self, conn, draft_with_prefixes):
        doc = generate_definition_document(conn,
                                           draft_with_prefixes["definition_id"])
        assert WORKFLOW_ID in doc

    def test_document_persisted(self, conn, draft_with_prefixes):
        did = draft_with_prefixes["definition_id"]
        generate_definition_document(conn, did)
        rec = get_ip_space_definition(conn, did)
        assert rec["definition_document"] != ""

    def test_document_customer_prefix_tagged(self, conn, draft):
        add_prefix(conn, draft["definition_id"], "203.0.113.0/24",
                   party_role="initiating", prefix_type="customer",
                   is_customer_prefix=True)
        doc = generate_definition_document(conn, draft["definition_id"])
        assert "[CUSTOMER]" in doc

    def test_document_no_prefixes(self, conn, draft):
        doc = generate_definition_document(conn, draft["definition_id"])
        assert "(none declared)" in doc

    def test_document_not_found_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            generate_definition_document(conn, str(uuid.uuid4()))

    def test_document_rejection_reason_included(self, conn, draft):
        reject_definition(conn, draft["definition_id"],
                          reason="Prefix overlap detected")
        doc = generate_definition_document(conn, draft["definition_id"])
        assert "Prefix overlap detected" in doc

    def test_document_routing_policy_notes(self, conn, draft):
        set_routing_policy(conn, draft["definition_id"],
                           notes="Reviewed by NOC 2026-06-27")
        doc = generate_definition_document(conn, draft["definition_id"])
        assert "Reviewed by NOC" in doc


# ── Full lifecycle integration ────────────────────────────────────────────────

class TestFullLifecycle:
    def test_happy_path_end_to_end(self, conn):
        d = create_ip_space_definition(
            conn,
            initiating_party_name="DHS",
            responding_party_name="DoD",
            peering_request_id="req-fed-001",
        )
        did = d["definition_id"]

        add_prefix(conn, did, "10.10.0.0/16",
                   party_role="initiating", prefix_type="aggregate")
        add_prefix(conn, did, "10.20.0.0/24",
                   party_role="initiating", prefix_type="customer",
                   is_customer_prefix=True, description="Tenant CISA")
        add_prefix(conn, did, "172.16.0.0/12",
                   party_role="responding", prefix_type="aggregate")

        set_routing_policy(conn, did,
                           max_prefixes_initiating=150,
                           max_prefixes_responding=100,
                           accepted_communities=["64500:100"],
                           no_export=True)

        rec = submit_definition(conn, did)
        assert rec["status"] == "submitted"

        rec = acknowledge_definition(conn, did)
        assert rec["status"] == "acknowledged"

        rec = approve_definition(conn, did, "initiating", notes="CISO sign-off")
        assert rec["status"] == "acknowledged"

        rec = approve_definition(conn, did, "responding", notes="NOC approval")
        assert rec["status"] == "verified"

        doc = generate_definition_document(conn, did)
        assert "DHS" in doc
        assert "DoD" in doc
        assert "10.10.0.0/16" in doc
        assert "172.16.0.0/12" in doc
        assert "[CUSTOMER]" in doc
        assert "150" in doc

    def test_reject_then_new(self, conn):
        d = create_ip_space_definition(conn, "Alpha", "Beta")
        add_prefix(conn, d["definition_id"], "192.0.2.0/24")
        submit_definition(conn, d["definition_id"])
        reject_definition(conn, d["definition_id"],
                          reason="Policy mismatch")

        d2 = create_ip_space_definition(conn, "Alpha", "Beta",
                                        peering_request_id="req-002")
        add_prefix(conn, d2["definition_id"], "192.0.2.0/24")
        submit_definition(conn, d2["definition_id"])
        acknowledge_definition(conn, d2["definition_id"])
        approve_definition(conn, d2["definition_id"], "initiating")
        result = approve_definition(conn, d2["definition_id"], "responding")
        assert result["status"] == "verified"

        rows = list_ip_space_definitions(conn, status="rejected")
        assert len(rows) == 1
        rows_v = list_ip_space_definitions(conn, status="verified")
        assert len(rows_v) == 1

    def test_workflow_and_step_constants(self, conn, draft):
        assert draft["workflow_id"] == "processify-wfl-f77f"
        assert draft["step"] == 3

    def test_default_routing_policy_structure(self):
        policy = _default_routing_policy()
        required_keys = {
            "max_prefixes_initiating",
            "max_prefixes_responding",
            "min_prefix_length_v4",
            "max_prefix_length_v4",
            "min_prefix_length_v6",
            "max_prefix_length_v6",
            "accepted_communities",
            "rejected_communities",
            "local_preference",
            "med",
            "no_export",
            "prefix_filter_action",
            "notes",
        }
        assert required_keys.issubset(set(policy.keys()))
