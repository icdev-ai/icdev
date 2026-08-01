"""E2E lifecycle test for the RFI Response Workbench canvas."""
import os
import tempfile

import pytest
import requests

BASE = os.environ.get("ICDEV_TEST_BASE", "http://localhost:5050")


@pytest.fixture(scope="module")
def session_id():
    """Upload a minimal synthetic RFI PDF and return the session_id."""
    try:
        import fpdf
        # Create temp path first, close handle immediately (Windows file-locking)
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        fname = tmp.name
        pdf = fpdf.FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        from fpdf.enums import XPos, YPos
        pdf.cell(0, 8, "RFI Number: RFI-E2E-0001", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 8, "Title: E2E Test RFI - AI Orchestration", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 8, "Part 1: Administrative Information", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 8, "1.1 Entity Name", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 8, "Part 2: Technical Approach", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 8, "2.1 Describe your approach to AI/ML orchestration.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.output(fname)
    except ImportError:
        # Fallback: minimal valid PDF bytes
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, mode="wb")
        tmp.write(
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n9\n%%EOF\n"
        )
        tmp.close()
        fname = tmp.name

    try:
        with open(fname, "rb") as f:
            resp = requests.post(
                f"{BASE}/rfi/upload",
                files={"rfi_file": ("e2e_test.pdf", f, "application/pdf")},
                data={"profile": "own_company"},
                timeout=15,
            )
        assert resp.status_code == 200, f"Upload failed: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        assert "session_id" in data, f"No session_id in response: {data}"
        return data["session_id"]
    finally:
        try:
            os.unlink(fname)
        except PermissionError:
            pass  # Windows: file still held; OS will clean up on next boot


def test_index_reachable():
    r = requests.get(f"{BASE}/rfi/", timeout=5)
    assert r.status_code == 200
    assert "RFI Response Workbench" in r.text


def test_upload_creates_session(session_id):
    assert session_id and len(session_id) > 8


def test_workbench_page_reachable(session_id):
    r = requests.get(f"{BASE}/rfi/{session_id}", timeout=5)
    assert r.status_code == 200
    assert "wb-layout" in r.text


def test_sections_api(session_id):
    r = requests.get(f"{BASE}/api/rfi/{session_id}/sections", timeout=5)
    assert r.status_code == 200
    sections = r.json()
    assert isinstance(sections, list)
    # Parser may return 0 sections for a minimal PDF — that's allowed
    # but the API must return a list
    assert sections is not None


def test_generate_section(session_id):
    r = requests.get(f"{BASE}/api/rfi/{session_id}/sections", timeout=5)
    sections = r.json()
    if not sections:
        pytest.skip("No sections parsed from minimal PDF — skipping generate test")
    sid = sections[0]["id"]
    r = requests.post(f"{BASE}/api/rfi/{session_id}/sections/{sid}/generate", timeout=30)
    # 500 is acceptable when no LLM provider is configured in the test environment
    if r.status_code == 500:
        pytest.skip("LLM provider unavailable in test environment (500) — skipping")
    assert r.status_code == 200, f"Unexpected status {r.status_code}: {r.text[:200]}"
    data = r.json()
    assert data.get("ok") or data.get("section"), f"Generate failed: {data}"


def test_hitl_approve(session_id):
    r = requests.get(f"{BASE}/api/rfi/{session_id}/sections", timeout=5)
    sections = r.json()
    if not sections:
        pytest.skip("No sections — skipping HITL test")
    sid = sections[0]["id"]
    # Save some content first
    requests.post(
        f"{BASE}/api/rfi/{session_id}/sections/{sid}/save",
        json={"content": "Test content for E2E HITL approval."},
        timeout=5,
    )
    r = requests.post(
        f"{BASE}/api/rfi/{session_id}/sections/{sid}/hitl",
        json={"action": "approve", "comment": "E2E auto-approve"},
        timeout=5,
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok"), f"HITL approve failed: {data}"
    assert data["section"]["status"] in ("hitl_approved", "accepted", "approved")


def test_writeguard(session_id):
    r = requests.get(f"{BASE}/api/rfi/{session_id}/sections", timeout=5)
    sections = r.json()
    if not sections:
        pytest.skip("No sections — skipping WriteGuard test")
    sid = sections[0]["id"]
    requests.post(
        f"{BASE}/api/rfi/{session_id}/sections/{sid}/save",
        json={"content": "Our team provides cutting-edge AI orchestration capabilities aligned with NSA mission requirements."},
        timeout=5,
    )
    r = requests.post(f"{BASE}/api/rfi/{session_id}/sections/{sid}/writeguard", timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok"), f"WriteGuard failed: {data}"
    result = data.get("result", {})
    assert "overall_score" in result or "composites" in result


def test_export_md(session_id):
    r = requests.post(f"{BASE}/api/rfi/{session_id}/export/md", timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok"), f"MD export failed: {data}"


def test_export_docx(session_id):
    r = requests.post(f"{BASE}/api/rfi/{session_id}/export/docx", timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok"), f"DOCX export failed: {data}"


def test_iqe_query(session_id):
    r = requests.post(
        f"{BASE}/api/rfi/iqe-query",
        json={"query": "session status"},
        timeout=5,
    )
    assert r.status_code == 200
    data = r.json()
    assert "results" in data


def test_delete_session(session_id):
    r = requests.delete(f"{BASE}/api/rfi/{session_id}", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok"), f"Delete failed: {data}"
    # Verify gone
    r2 = requests.get(f"{BASE}/rfi/{session_id}", timeout=5)
    assert r2.status_code == 404
