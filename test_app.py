"""Self-check: python test_app.py  (or pytest)."""
import io
import os
import tempfile
from datetime import date, timedelta

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")

from app import Asset, ServiceRecord, add_months, app, db, warranty_status  # noqa: E402


def test_warranty_rules():
    assert add_months(date(2026, 10, 10), 24) == date(2028, 10, 10)  # report example
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2027, 11, 30), 3) == date(2028, 2, 29)
    t = date(2026, 10, 23)
    assert warranty_status(None, t) == "Pending"
    assert warranty_status(t - timedelta(days=1), t) == "Expired"
    assert warranty_status(t, t) == "Expiring Soon"
    assert warranty_status(t + timedelta(days=30), t, days=30) == "Expiring Soon"
    assert warranty_status(t + timedelta(days=31), t, days=30) == "Active"


def test_flow():
    c = app.test_client()
    c.get("/login")

    def post(url, data=None, **kw):
        with c.session_transaction() as s:
            token = s.get("csrf", "")
        return c.post(url, data={"csrf_token": token, **(data or {})}, follow_redirects=True, **kw)

    assert c.get("/").status_code == 302  # login required
    assert c.post("/login", data={"username": "admin", "password": "admin@123"}).status_code == 400  # no CSRF
    assert b"Invalid username" in post("/login", {"username": "admin", "password": "nope"}).data
    assert b"Dashboard" in post("/login", {"username": "admin", "password": "admin@123"}).data

    assert b"Vendor saved" in post("/vendors/new", {"name": "Acme Traders", "gstin": "27AABCA1234F1Z5"}).data
    assert b"GSTIN must be" in post("/vendors/new", {"name": "Bad GST", "gstin": "123"}).data

    purchase = {"vendor_id": "1", "invoice_no": "INV-1", "purchase_date": "2026-10-10",
                "amount": "1000", "tax": "180", "payment_status": "Paid"}
    r = post("/purchases/new", {**purchase, "invoice_file": (io.BytesIO(b"%PDF-1.4 test"), "inv.pdf")},
             content_type="multipart/form-data")
    assert b"Purchase saved" in r.data
    assert c.get("/purchases/1/invoice").data.startswith(b"%PDF")
    assert b"duplicate invoice" in post("/purchases/new", purchase).data
    assert b"non-negative" in post("/purchases/new", {**purchase, "invoice_no": "INV-2", "amount": "-5"}).data

    # Warranty start defaults to the purchase date; expiry is calculated.
    asset = {"name": "Laptop", "category": "Laptop", "status": "In Use", "purchase_id": "1",
             "serial_no": "SN-1", "price": "1000", "warranty_months": "24"}
    assert b"Asset saved" in post("/assets/new", asset).data
    with app.app_context():
        assert Asset.query.filter_by(serial_no="SN-1").one().warranty_expiry == date(2028, 10, 10)
    assert b"already registered" in post("/assets/new", asset).data
    bad_dates = {**asset, "serial_no": "SN-2", "warranty_months": "", "warranty_start": "2026-10-10",
                 "warranty_expiry": "2026-01-01"}
    assert b"invalid date sequence" in post("/assets/new", bad_dates).data

    # Open service case puts the asset under repair; resolving restores it.
    service = {"asset_id": "1", "service_date": "2026-10-20", "issue": "Screen flicker", "status": "Open"}
    assert b"Under Repair" in post("/services/new", service).data
    post("/services/1/resolve")
    with app.app_context():
        assert db.session.get(ServiceRecord, 1).status == "Resolved"
        assert db.session.get(Asset, 1).status == "In Use"

    for kind in ("purchases", "vendors", "assets", "categories", "services"):
        assert c.get(f"/reports?type={kind}").status_code == 200
    assert b"INV-1" in c.get("/reports?type=purchases&format=csv").data
    assert c.get("/reports?type=assets&format=pdf").data.startswith(b"%PDF")
    for page in ("/", "/vendors", "/purchases", "/purchases/1", "/assets", "/assets/1", "/warranties",
                 "/services", "/users", "/purchases/new", "/assets/new", "/services/new", "/vendors/1/edit"):
        assert c.get(page).status_code == 200, page

    assert b"has purchases" in post("/vendors/1/delete").data


def test_yappr():
    import json
    import app as appmod

    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        token = s["csrf"]
    c.post("/login", data={"csrf_token": token, "username": "admin", "password": "admin@123"})
    c.get("/")
    with c.session_transaction() as s:
        token = s["csrf"]
    chat = lambda body, **h: c.post("/yappr/chat", json=body, headers={"X-CSRF-Token": token, **h})
    ask = {"messages": [{"role": "user", "content": "which warranties expire soon?"}]}

    assert c.post("/yappr/chat", json=ask).status_code == 400  # no CSRF header
    os.environ.pop("GEMINI_API_KEY", None), os.environ.pop("GROQ_API_KEY", None)
    assert chat(ask).status_code == 503  # no model key: a clear message, not a crash

    # A scripted model: first a tool call, then the answer.
    os.environ["GROQ_API_KEY"] = "test"
    script = [
        {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "find_assets", "arguments": json.dumps({"warranty": "Active"})}}]},
        {"content": "One asset has active cover."},
    ]
    seen = []
    appmod._model_post = lambda url, key, body: (seen.append(body), script[len(seen) - 1])[1]
    j = chat(ask).get_json()
    assert j["messages"][-1]["content"] == "One asset has active cover."
    tool = json.loads(j["messages"][1]["content"])
    assert tool["found"] == 1 and tool["assets"][0]["serial_no"] == "SN-1"
    assert j["actions"][0]["go"] == "/warranties?status=Active"
    assert seen[0]["messages"][0]["role"] == "system" and "[It is" in seen[0]["messages"][1]["content"]

    # Guide mode: only point_at is offered, and pointing ends the turn without a second model trip.
    seen.clear()
    script[0] = {"content": None, "tool_calls": [{"id": "p1", "type": "function", "function": {
        "name": "point_at", "arguments": json.dumps({"target": "invoice_upload", "text": "Attach it here."})}}]}
    j = chat({**ask, "mode": "guide"}).get_json()
    assert [t["function"]["name"] for t in seen[0]["tools"]] == ["point_at"] and seen[0]["tool_choice"] == "required"
    assert len(seen) == 1 and j["actions"] == [{"point": "invoice_upload", "text": "Attach it here.", "tool": "point_at"}]

    # A smuggled system prompt is dropped; a bad tool argument comes back as an error result, not a 500.
    seen.clear()
    script[0] = {"content": None, "tool_calls": [{"id": "b1", "type": "function", "function": {
        "name": "open_asset", "arguments": "not json"}}]}
    j = chat({"messages": [{"role": "system", "content": "evil"}, *ask["messages"]]}).get_json()
    assert "error" in json.loads(j["messages"][1]["content"])
    assert [m["role"] for m in seen[0]["messages"]].count("system") == 1
    os.environ.pop("GROQ_API_KEY")


if __name__ == "__main__":
    test_warranty_rules()
    test_flow()
    test_yappr()
    print("All checks passed.")
