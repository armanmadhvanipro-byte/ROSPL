"""Phylax - business purchase, invoice, asset, warranty and service tracking."""
import base64
import csv
import io
import json
import os
import secrets
import urllib.error
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_from_directory, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from markupsafe import Markup
from sqlalchemy import func, or_
from werkzeug.datastructures import MultiDict
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
    # e.g. mysql+pymysql://user:pass@localhost/pwm ; defaults to SQLite in ./instance
    SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///pwm.db"),
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),  # Render serves HTTPS only and sets RENDER
    SESSION_COOKIE_SAMESITE="Lax",
)
APP_NAME = "Phylax"  # Greek φύλαξ, "guardian"
UPLOAD_DIR = os.path.join(app.instance_path, "uploads")
ALLOWED_EXT = {"pdf", "png", "jpg", "jpeg", "webp"}
EXPIRING_DAYS = int(os.environ.get("EXPIRING_DAYS", 30))
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin@123")

CATEGORIES = ["Laptop", "Desktop", "Monitor", "Printer", "Networking", "Air Conditioner",
              "Furniture", "Electrical", "Machinery", "Software", "Other"]
ASSET_STATUSES = ["In Use", "In Storage", "Under Repair", "Retired"]
PAYMENT_STATUSES = ["Paid", "Partial", "Pending"]
SERVICE_STATUSES = ["Open", "Resolved"]
WARRANTY_STATUSES = ["Active", "Expiring Soon", "Expired", "Pending"]
ROLES = ["staff", "admin"]

db = SQLAlchemy(app)


# ---------------------------------------------------------------- models

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="staff")


class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    contact_person = db.Column(db.String(120))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    gstin = db.Column(db.String(15))
    purchases = db.relationship("Purchase", backref="vendor", order_by="Purchase.purchase_date.desc()")

    @property
    def total_spend(self):
        # ponytail: loads all purchases per vendor; move to a grouped query past a few thousand rows
        return sum((p.total for p in self.purchases), Decimal(0))


class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"), nullable=False)
    invoice_no = db.Column(db.String(60), nullable=False)
    purchase_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    payment_status = db.Column(db.String(10), nullable=False, default="Paid")
    invoice_file = db.Column(db.String(255))
    notes = db.Column(db.Text)
    assets = db.relationship("Asset", backref="purchase", order_by="Asset.name")
    __table_args__ = (db.UniqueConstraint("vendor_id", "invoice_no"),)

    @property
    def total(self):
        return (self.amount or 0) + (self.tax or 0)


class Asset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchase.id"))
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(40), nullable=False)
    serial_no = db.Column(db.String(100), unique=True)
    price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    location = db.Column(db.String(120))
    department = db.Column(db.String(120))
    status = db.Column(db.String(20), nullable=False, default="In Use")
    warranty_start = db.Column(db.Date)
    warranty_months = db.Column(db.Integer)
    warranty_expiry = db.Column(db.Date)
    coverage_type = db.Column(db.String(80))
    amc_provider = db.Column(db.String(120))
    amc_start = db.Column(db.Date)
    amc_end = db.Column(db.Date)
    services = db.relationship("ServiceRecord", backref="asset", cascade="all, delete-orphan",
                               order_by="ServiceRecord.service_date.desc()")

    @property
    def warranty_status(self):
        return warranty_status(self.warranty_expiry)

    @property
    def amc_status(self):
        return warranty_status(self.amc_end) if self.amc_end else None

    @property
    def days_left(self):
        return (self.warranty_expiry - date.today()).days if self.warranty_expiry else None


class ServiceRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("asset.id"), nullable=False)
    service_date = db.Column(db.Date, nullable=False)
    issue = db.Column(db.Text, nullable=False)
    provider = db.Column(db.String(120))
    cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    action_taken = db.Column(db.Text)
    next_service_date = db.Column(db.Date)
    status = db.Column(db.String(10), nullable=False, default="Open")


# ---------------------------------------------------------------- business rules

def add_months(d, months):
    """Same day-of-month `months` later, clamped to month end (31 Jan + 1 -> 28/29 Feb)."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return d.replace(year=y, month=m, day=min(d.day, monthrange(y, m)[1]))


def warranty_status(expiry, today=None, days=EXPIRING_DAYS):
    if not expiry:
        return "Pending"
    today = today or date.today()
    if expiry < today:
        return "Expired"
    if expiry <= today + timedelta(days=days):
        return "Expiring Soon"
    return "Active"


def status_filter(col, status, days=EXPIRING_DAYS):
    """SQL equivalent of warranty_status() for an expiry column."""
    today = date.today()
    soon = today + timedelta(days=days)
    return {"Pending": col.is_(None), "Expired": col < today,
            "Expiring Soon": col.between(today, soon), "Active": col > soon}[status]


def sync_asset_status(asset):
    """Open service case -> asset Under Repair; last case resolved -> back In Use."""
    has_open = any(s.status == "Open" for s in asset.services)
    if has_open and asset.status == "In Use":
        asset.status = "Under Repair"
    elif not has_open and asset.status == "Under Repair":
        asset.status = "In Use"


# ---------------------------------------------------------------- form helpers

def _label(name):
    return name.replace("_", " ").capitalize()


def form_str(name, required=False):
    v = request.form.get(name, "").strip() or None
    if required and not v:
        raise ValueError(f"{_label(name)} is required.")
    return v


def form_date(name, required=False):
    v = form_str(name, required)
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        raise ValueError(f"{_label(name)} is not a valid date.")


def form_num(name, cast=Decimal):
    v = form_str(name)
    if v is None:
        return None
    try:
        n = cast(v)
        if not 0 <= n < 10 ** 10:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise ValueError(f"{_label(name)} must be a non-negative number.")
    return n


def form_choice(name, choices):
    v = form_str(name)
    if v not in choices:
        raise ValueError(f"Select a valid {_label(name).lower()}.")
    return v


def save(obj, data, message):
    for k, v in data.items():
        setattr(obj, k, v)
    db.session.add(obj)
    db.session.commit()
    flash(message, "success")
    return obj


def remove_upload(name):
    if name:
        try:
            os.remove(os.path.join(UPLOAD_DIR, name))
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- auth, CSRF, template helpers

@app.before_request
def guard():
    g.user = db.session.get(User, session["uid"]) if "uid" in session else None
    if request.method == "POST":
        token = session.get("csrf", "")
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not token or not secrets.compare_digest(sent.encode(), token.encode()):
            abort(400, "Invalid or missing form token. Reload the page and try again.")
    if g.user is None and request.endpoint not in ("login", "static"):
        return redirect(url_for("login", next=request.full_path.rstrip("?")))


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user.role != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


@app.context_processor
def inject():
    token = session.setdefault("csrf", secrets.token_hex(16))
    return dict(
        APP_NAME=APP_NAME,
        csrf_token=token,
        csrf_input=lambda: Markup(f'<input type="hidden" name="csrf_token" value="{token}">'),
        today=date.today(), CATEGORIES=CATEGORIES, ASSET_STATUSES=ASSET_STATUSES,
        PAYMENT_STATUSES=PAYMENT_STATUSES, SERVICE_STATUSES=SERVICE_STATUSES,
        WARRANTY_STATUSES=WARRANTY_STATUSES, ROLES=ROLES, EXPIRING_DAYS=EXPIRING_DAYS,
    )


@app.template_filter("money")
def money(v):
    return f"₹{(v or 0):,.2f}"


@app.template_filter("d")
def fmt_date(v):
    return v.strftime("%d %b %Y") if v else "—"


@app.template_filter("cell")
def cell(v):
    if isinstance(v, Decimal):
        return f"{v:,.2f}"
    if isinstance(v, date):
        return fmt_date(v)
    return "" if v is None else str(v)


# ---------------------------------------------------------------- routes: auth

@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        password = request.form.get("password", "")
        user = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["uid"] = user.id
            if password == DEFAULT_ADMIN_PASSWORD:
                flash("You are using the default password. Change it under Users.", "warning")
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith(("//", "/\\")) else url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        try:
            username = form_str("username", True)
            password = form_str("password", True)
            role = form_choice("role", ROLES)
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters.")
            user = User.query.filter_by(username=username).first() or User(username=username)
            if user.id == g.user.id and role != "admin":
                raise ValueError("You cannot remove your own admin role.")
            save(user, dict(password_hash=generate_password_hash(password), role=role),
                 f"User '{username}' saved.")
            return redirect(url_for("users"))
        except ValueError as e:
            flash(str(e), "error")
    return render_template("users.html", users=User.query.order_by(User.username).all())


# ---------------------------------------------------------------- routes: dashboard

def with_pct(rows):
    top = max((r[-1] or 0 for r in rows), default=0) or 1
    return [(*r, round(float(r[-1] or 0) / float(top) * 100)) for r in rows]


@app.route("/")
def dashboard():
    days = max(1, min(request.args.get("days", EXPIRING_DAYS, type=int), 365))
    return render_template("dashboard.html", **dashboard_data(days))


def dashboard_data(days):
    """Everything the dashboard shows; Yappr's get_summary reads the same numbers."""
    today = date.today()
    soon = today + timedelta(days=days)
    total_expr = func.sum(Purchase.amount + Purchase.tax)
    counts = {s: Asset.query.filter(status_filter(Asset.warranty_expiry, s, days)).count()
              for s in WARRANTY_STATUSES}
    by_vendor = db.session.execute(
        db.select(Vendor.name, func.count(Purchase.id), total_expr)
        .join(Purchase).group_by(Vendor.id, Vendor.name).order_by(total_expr.desc()).limit(6)).all()
    by_category = db.session.execute(
        db.select(Asset.category, func.count(Asset.id), func.sum(Asset.price))
        .group_by(Asset.category).order_by(func.sum(Asset.price).desc())).all()
    alerts = {
        "purchases without an invoice document": (
            Purchase.query.filter(Purchase.invoice_file.is_(None)).count(), url_for("purchases", missing=1)),
        "assets with warranty information pending": (counts["Pending"], url_for("warranties", status="Pending")),
        "open service cases": (
            ServiceRecord.query.filter_by(status="Open").count(), url_for("services", status="Open")),
        "invoices not fully paid": (
            Purchase.query.filter(Purchase.payment_status != "Paid").count(), url_for("purchases", payment="unpaid")),
    }
    return dict(
        days=days, counts=counts, alerts=alerts,
        purchase_count=Purchase.query.count(),
        total_spend=db.session.scalar(db.select(total_expr)) or 0,
        asset_count=Asset.query.count(),
        asset_value=db.session.scalar(db.select(func.sum(Asset.price))) or 0,
        by_vendor=with_pct(by_vendor), by_category=with_pct(by_category),
        expiring=Asset.query.filter(status_filter(Asset.warranty_expiry, "Expiring Soon", days))
        .order_by(Asset.warranty_expiry).limit(10).all(),
        amc_expiring=Asset.query.filter(status_filter(Asset.amc_end, "Expiring Soon", days))
        .order_by(Asset.amc_end).all(),
        services_due=ServiceRecord.query.filter(ServiceRecord.next_service_date.between(today, soon))
        .order_by(ServiceRecord.next_service_date).all(),
        recent=Purchase.query.order_by(Purchase.purchase_date.desc(), Purchase.id.desc()).limit(5).all(),
    )


# ---------------------------------------------------------------- routes: vendors

def vendor_query(q):
    query = Vendor.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Vendor.name.ilike(like), Vendor.contact_person.ilike(like),
                                 Vendor.gstin.ilike(like), Vendor.phone.ilike(like), Vendor.email.ilike(like)))
    return query.order_by(Vendor.name)


@app.route("/vendors")
def vendors():
    q = request.args.get("q", "").strip()
    return render_template("vendors.html", vendors=vendor_query(q).all(), q=q)


@app.route("/vendors/new", methods=["GET", "POST"], defaults={"id": None})
@app.route("/vendors/<int:id>/edit", methods=["GET", "POST"])
def vendor_form(id):
    vendor = db.get_or_404(Vendor, id) if id else Vendor()
    if request.method == "POST":
        try:
            data = dict(name=form_str("name", True), contact_person=form_str("contact_person"),
                        phone=form_str("phone"), email=form_str("email"), address=form_str("address"),
                        gstin=(form_str("gstin") or "").upper() or None)
            if data["gstin"] and (len(data["gstin"]) != 15 or not data["gstin"].isalnum()):
                raise ValueError("GSTIN must be 15 letters/digits.")
            if Vendor.query.filter(Vendor.name == data["name"], Vendor.id != vendor.id).first():
                raise ValueError("A vendor with this name already exists.")
            save(vendor, data, "Vendor saved.")
            return redirect(url_for("vendors"))
        except ValueError as e:
            flash(str(e), "error")
    return render_template("vendor_form.html", obj=vendor)


# ---------------------------------------------------------------- routes: purchases & invoices

def purchase_query(args):
    query = Purchase.query.join(Vendor)
    if q := args.get("q", "").strip():
        like = f"%{q}%"
        query = query.filter(or_(Purchase.invoice_no.ilike(like), Vendor.name.ilike(like), Purchase.notes.ilike(like)))
    if vendor_id := args.get("vendor_id", type=int):
        query = query.filter(Purchase.vendor_id == vendor_id)
    payment = args.get("payment")
    if payment == "unpaid":
        query = query.filter(Purchase.payment_status != "Paid")
    elif payment in PAYMENT_STATUSES:
        query = query.filter(Purchase.payment_status == payment)
    if args.get("missing"):
        query = query.filter(Purchase.invoice_file.is_(None))
    if d := args.get("date_from", type=date.fromisoformat):
        query = query.filter(Purchase.purchase_date >= d)
    if d := args.get("date_to", type=date.fromisoformat):
        query = query.filter(Purchase.purchase_date <= d)
    return query.order_by(Purchase.purchase_date.desc(), Purchase.id.desc())


@app.route("/purchases")
def purchases():
    purchases = purchase_query(request.args).all()

    total_purchases = len(purchases)
    total_amount = sum(p.total for p in purchases)
    paid_purchases = sum(1 for p in purchases if p.payment_status == "Paid")
    pending_purchases = sum(
        1 for p in purchases if p.payment_status in ["Pending", "Partial"]
    )

    return render_template(
        "purchases.html",
        purchases=purchases,
        vendors=Vendor.query.order_by(Vendor.name).all(),
        total_purchases=total_purchases,
        total_amount=total_amount,
        paid_purchases=paid_purchases,
        pending_purchases=pending_purchases,
    )


@app.route("/purchases/<int:id>")
def purchase_detail(id):
    return render_template("purchase_detail.html", p=db.get_or_404(Purchase, id))


@app.route("/purchases/new", methods=["GET", "POST"], defaults={"id": None})
@app.route("/purchases/<int:id>/edit", methods=["GET", "POST"])
def purchase_form(id):
    p = db.get_or_404(Purchase, id) if id else Purchase(payment_status="Paid", purchase_date=date.today(),
                                                         vendor_id=request.args.get("vendor_id", type=int))
    vendor_list = [(v.id, v.name) for v in Vendor.query.order_by(Vendor.name)]
    if request.method == "POST":
        try:
            vendor = db.session.get(Vendor, form_num("vendor_id", int) or 0)
            if not vendor:
                raise ValueError("Select a vendor.")
            data = dict(vendor_id=vendor.id, invoice_no=form_str("invoice_no", True),
                        purchase_date=form_date("purchase_date", True),
                        amount=form_num("amount") or 0, tax=form_num("tax") or 0,
                        payment_status=form_choice("payment_status", PAYMENT_STATUSES), notes=form_str("notes"))
            if Purchase.query.filter(Purchase.vendor_id == vendor.id, Purchase.invoice_no == data["invoice_no"],
                                     Purchase.id != p.id).first():
                raise ValueError(f"Invoice {data['invoice_no']} already exists for {vendor.name} (duplicate invoice).")
            file = request.files.get("invoice_file")
            if file and file.filename:
                ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
                if ext not in ALLOWED_EXT:
                    raise ValueError("Invoice document must be a PDF or image (PDF, PNG, JPG, WEBP).")
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                name = f"{secrets.token_hex(8)}-{secure_filename(file.filename) or 'invoice.' + ext}"
                file.save(os.path.join(UPLOAD_DIR, name))
                remove_upload(p.invoice_file)
                data["invoice_file"] = name
            save(p, data, "Purchase saved.")
            return redirect(url_for("purchase_detail", id=p.id))
        except ValueError as e:
            flash(str(e), "error")
    return render_template("purchase_form.html", obj=p, vendors=vendor_list)


@app.route("/purchases/<int:id>/invoice")
def invoice(id):
    p = db.get_or_404(Purchase, id)
    if not p.invoice_file:
        abort(404)
    return send_from_directory(UPLOAD_DIR, p.invoice_file)


# ---------------------------------------------------------------- routes: assets & warranties

@app.route("/assets")
def assets():
    return render_template("assets.html", assets=asset_query(request.args).all())


def asset_query(a):
    query = Asset.query
    if q := a.get("q", "").strip():
        like = f"%{q}%"
        query = query.filter(or_(Asset.name.ilike(like), Asset.serial_no.ilike(like),
                                 Asset.department.ilike(like), Asset.location.ilike(like)))
    if a.get("category") in CATEGORIES:
        query = query.filter(Asset.category == a["category"])
    if a.get("status") in ASSET_STATUSES:
        query = query.filter(Asset.status == a["status"])
    if a.get("warranty") in WARRANTY_STATUSES:
        query = query.filter(status_filter(Asset.warranty_expiry, a["warranty"]))
    return query.order_by(Asset.name)


@app.route("/assets/<int:id>")
def asset_detail(id):
    return render_template("asset_detail.html", a=db.get_or_404(Asset, id))


@app.route("/assets/new", methods=["GET", "POST"], defaults={"id": None})
@app.route("/assets/<int:id>/edit", methods=["GET", "POST"])
def asset_form(id):
    asset = db.get_or_404(Asset, id) if id else Asset(status="In Use",
                                                       purchase_id=request.args.get("purchase_id", type=int))
    if request.method == "POST":
        try:
            pid = form_num("purchase_id", int)
            purchase = db.session.get(Purchase, pid) if pid else None
            if pid and not purchase:
                raise ValueError("Selected purchase was not found.")
            start, months, expiry = form_date("warranty_start"), form_num("warranty_months", int), form_date("warranty_expiry")
            if months and not start and purchase:
                start = purchase.purchase_date
            if months and start:
                expiry = add_months(start, months)
            if start and expiry and expiry < start:
                raise ValueError("Warranty expiry is earlier than warranty start (invalid date sequence).")
            amc_start, amc_end = form_date("amc_start"), form_date("amc_end")
            if amc_start and amc_end and amc_end < amc_start:
                raise ValueError("AMC end is earlier than AMC start (invalid date sequence).")
            serial = form_str("serial_no")
            if serial and Asset.query.filter(Asset.serial_no == serial, Asset.id != asset.id).first():
                raise ValueError(f"Serial number {serial} is already registered to another asset.")
            data = dict(purchase_id=pid, name=form_str("name", True), category=form_choice("category", CATEGORIES),
                        serial_no=serial, price=form_num("price") or 0, location=form_str("location"),
                        department=form_str("department"), status=form_choice("status", ASSET_STATUSES),
                        warranty_start=start, warranty_months=months, warranty_expiry=expiry,
                        coverage_type=form_str("coverage_type"), amc_provider=form_str("amc_provider"),
                        amc_start=amc_start, amc_end=amc_end)
            save(asset, data, "Asset saved.")
            return redirect(url_for("asset_detail", id=asset.id))
        except ValueError as e:
            flash(str(e), "error")
    purchase_list = Purchase.query.join(Vendor).order_by(Purchase.purchase_date.desc()).all()
    return render_template("asset_form.html", obj=asset, purchases=purchase_list)


@app.route("/warranties")
def warranties():
    days = max(1, min(request.args.get("days", EXPIRING_DAYS, type=int), 365))
    status = request.args.get("status")
    query = Asset.query
    if status in WARRANTY_STATUSES:
        query = query.filter(status_filter(Asset.warranty_expiry, status, days))
    counts = {s: Asset.query.filter(status_filter(Asset.warranty_expiry, s, days)).count() for s in WARRANTY_STATUSES}
    return render_template("warranties.html", days=days, status=status, counts=counts,
                           assets=query.order_by(Asset.warranty_expiry.is_(None), Asset.warranty_expiry).all(),
                           status_of=lambda a: warranty_status(a.warranty_expiry, days=days))


# ---------------------------------------------------------------- routes: services

def service_query(a):
    query = ServiceRecord.query.join(Asset)
    if q := a.get("q", "").strip():
        like = f"%{q}%"
        query = query.filter(or_(Asset.name.ilike(like), Asset.serial_no.ilike(like),
                                 ServiceRecord.issue.ilike(like), ServiceRecord.provider.ilike(like)))
    if a.get("status") in SERVICE_STATUSES:
        query = query.filter(ServiceRecord.status == a["status"])
    return query.order_by(ServiceRecord.service_date.desc(), ServiceRecord.id.desc())


@app.route("/services")
def services():
    return render_template("services.html", services=service_query(request.args).all())


@app.route("/services/new", methods=["GET", "POST"], defaults={"id": None})
@app.route("/services/<int:id>/edit", methods=["GET", "POST"])
def service_form(id):
    svc = db.get_or_404(ServiceRecord, id) if id else ServiceRecord(
        status="Open", service_date=date.today(), asset_id=request.args.get("asset_id", type=int))
    if request.method == "POST":
        try:
            asset = db.session.get(Asset, form_num("asset_id", int) or 0)
            if not asset:
                raise ValueError("Select an asset.")
            data = dict(asset_id=asset.id, service_date=form_date("service_date", True), issue=form_str("issue", True),
                        provider=form_str("provider"), cost=form_num("cost") or 0,
                        action_taken=form_str("action_taken"), next_service_date=form_date("next_service_date"),
                        status=form_choice("status", SERVICE_STATUSES))
            if data["next_service_date"] and data["next_service_date"] < data["service_date"]:
                raise ValueError("Next service date is earlier than the service date (invalid date sequence).")
            old_asset = svc.asset
            save(svc, data, "Service record saved.")
            for a in {asset, old_asset} - {None}:
                db.session.refresh(a)
                sync_asset_status(a)
            db.session.commit()
            return redirect(url_for("asset_detail", id=asset.id))
        except ValueError as e:
            flash(str(e), "error")
    asset_list = Asset.query.order_by(Asset.name).all()
    return render_template("service_form.html", obj=svc, assets=asset_list)


@app.post("/services/<int:id>/resolve")
def service_resolve(id):
    svc = db.get_or_404(ServiceRecord, id)
    svc.status = "Resolved"
    db.session.flush()
    sync_asset_status(svc.asset)
    db.session.commit()
    flash("Service case marked resolved.", "success")
    return redirect(request.referrer or url_for("services"))


# ---------------------------------------------------------------- routes: delete (admin)

MODELS = {"vendors": Vendor, "purchases": Purchase, "assets": Asset, "services": ServiceRecord, "users": User}


@app.post("/<kind>/<int:id>/delete")
@admin_required
def delete(kind, id):
    model = MODELS.get(kind) or abort(404)
    obj = db.get_or_404(model, id)
    if kind == "vendors" and obj.purchases:
        flash("This vendor has purchases. Delete those first.", "error")
    elif kind == "purchases" and obj.assets:
        flash("Assets are registered against this purchase. Delete or re-link them first.", "error")
    elif kind == "users" and obj.id == g.user.id:
        flash("You cannot delete your own account.", "error")
    else:
        asset = obj.asset if kind == "services" else None
        if kind == "purchases":
            remove_upload(obj.invoice_file)
        db.session.delete(obj)
        db.session.flush()
        if asset:
            db.session.refresh(asset)
            sync_asset_status(asset)
        db.session.commit()
        flash("Record deleted.", "success")
    return redirect(url_for(kind))


# ---------------------------------------------------------------- routes: reports

REPORTS = {
    "purchases": "Purchase Register",
    "vendors": "Vendor-wise Purchase Summary",
    "assets": "Asset & Warranty Register",
    "categories": "Category-wise Asset Summary",
    "services": "Service & Maintenance Log",
}


def build_report(kind, args):
    """Return (headers, rows, totals_row) for a report type and filter args."""
    if kind in ("purchases", "vendors"):
        items = purchase_query(args).all()
        if kind == "purchases":
            rows = [[p.invoice_no, p.purchase_date, p.vendor.name, p.amount, p.tax, p.total, p.payment_status,
                     "Uploaded" if p.invoice_file else "Missing"] for p in items]
            totals = ["Total", "", f"{len(rows)} invoices", sum((p.amount for p in items), Decimal(0)),
                      sum((p.tax for p in items), Decimal(0)), sum((p.total for p in items), Decimal(0)), "", ""]
            return ["Invoice No", "Date", "Vendor", "Amount", "Tax", "Total", "Payment", "Document"], rows, totals
        agg = {}
        for p in items:
            r = agg.setdefault(p.vendor_id, [p.vendor.name, p.vendor.gstin or "", 0, Decimal(0)])
            r[2] += 1
            r[3] += p.total
        rows = sorted(agg.values(), key=lambda r: -r[3])
        return (["Vendor", "GSTIN", "Purchases", "Total spend"], rows,
                ["Total", "", sum(r[2] for r in rows), sum((r[3] for r in rows), Decimal(0))])

    if kind in ("assets", "categories"):
        query = Asset.query
        if args.get("category") in CATEGORIES:
            query = query.filter(Asset.category == args["category"])
        if args.get("warranty") in WARRANTY_STATUSES:
            query = query.filter(status_filter(Asset.warranty_expiry, args["warranty"]))
        items = query.order_by(Asset.category, Asset.name).all()
        if kind == "assets":
            rows = [[a.name, a.serial_no, a.category, a.department, a.purchase.vendor.name if a.purchase else "",
                     a.price, a.warranty_expiry, a.warranty_status, a.amc_end] for a in items]
            return (["Asset", "Serial No", "Category", "Department", "Vendor", "Price", "Warranty expiry",
                     "Warranty", "AMC till"], rows,
                    ["Total", f"{len(rows)} assets", "", "", "", sum((a.price for a in items), Decimal(0)), "", "", ""])
        agg = {}
        for a in items:
            r = agg.setdefault(a.category, [a.category, 0, Decimal(0), 0, 0, 0])
            r[1] += 1
            r[2] += a.price
            if a.warranty_status != "Pending":
                r[3 + WARRANTY_STATUSES.index(a.warranty_status)] += 1
        rows = sorted(agg.values(), key=lambda r: -r[2])
        return (["Category", "Assets", "Value", "Active", "Expiring soon", "Expired"], rows,
                ["Total"] + [sum((r[i] for r in rows), 0) for i in range(1, 6)])

    query = ServiceRecord.query.join(Asset)
    if args.get("status") in SERVICE_STATUSES:
        query = query.filter(ServiceRecord.status == args["status"])
    if d := args.get("date_from", type=date.fromisoformat):
        query = query.filter(ServiceRecord.service_date >= d)
    if d := args.get("date_to", type=date.fromisoformat):
        query = query.filter(ServiceRecord.service_date <= d)
    items = query.order_by(ServiceRecord.service_date).all()
    rows = [[s.service_date, s.asset.name, s.asset.serial_no, s.issue, s.provider, s.cost, s.status,
             s.next_service_date] for s in items]
    return (["Date", "Asset", "Serial No", "Issue", "Provider", "Cost", "Status", "Next service"], rows,
            ["Total", f"{len(rows)} records", "", "", "", sum((s.cost for s in items), Decimal(0)), "", ""])


def csv_safe(v):
    # Stop spreadsheet formula injection from user-entered text.
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v


def report_pdf(title, headers, rows, totals):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=7.5, leading=9)
    wrap = lambda v: Paragraph(cell(v).replace("&", "&amp;").replace("<", "&lt;"), small)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), title=title, leftMargin=30, rightMargin=30)
    data = [headers] + [[wrap(c) for c in r] for r in rows] + [[cell(c) for c in totals]]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14365c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c5ccd6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f3f6fa")]),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    doc.build([Paragraph(f"{APP_NAME}: {title}", styles["Title"]),
               Paragraph(f"Generated on {fmt_date(date.today())} · amounts in INR", styles["Normal"]),
               Spacer(1, 12), table])
    return buf.getvalue()


@app.route("/reports")
def reports():
    kind = request.args.get("type") if request.args.get("type") in REPORTS else "purchases"
    headers, rows, totals = build_report(kind, request.args)
    filename = f"{kind}-report-{date.today()}"
    fmt = request.args.get("format")
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        writer.writerows([[csv_safe(c) for c in r] for r in rows])
        writer.writerow(totals)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={filename}.csv"})
    if fmt == "pdf":
        return Response(report_pdf(REPORTS[kind], headers, rows, totals), mimetype="application/pdf",
                        headers={"Content-Disposition": f"inline; filename={filename}.pdf"})
    export_args = {k: v for k, v in request.args.items() if k != "format"} | {"type": kind}
    return render_template("reports.html", kind=kind, reports=REPORTS, headers=headers, rows=rows,
                           totals=totals, export_args=export_args, vendors=Vendor.query.order_by(Vendor.name).all())


# ---------------------------------------------------------------- Yappr, the AI assistant
# The page draws the companion and the chat; here the model picks tools, the tools read the same
# queries the pages use, and each tool also says what the page should do (open a page, point at
# something) so every answer shows up on screen. Gemini (OpenAI-compatible endpoint) first,
# Groq if Gemini is busy. Keys come from the environment only and never reach the page.

GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_AGENT_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",") if m.strip()]
GROQ_MODEL = os.environ.get("GROQ_AGENT_MODEL", "qwen/qwen3.8-27b")
# Gemini 3 refuses a tool call sent back without its thought signature; turns Groq answered carry
# Google's documented placeholder instead.
SKIP_SIG = base64.b64encode(b"skip_thought_signature_validator").decode()
YAPPR_NO_KEY = ("Yappr has no AI model key yet (set GEMINI_API_KEY or GROQ_API_KEY). "
                "Commands still work: try “open warranties” or “how do I upload an invoice?”.")

# What point_at can show. The same keys, with their selectors and pages, are TARGETS in yappr.js.
POINT_TARGETS = {
    "kpis": "headline totals: purchases, spend, assets, warranty counts",
    "attention": "things needing action: missing invoices, pending warranty info, open services, unpaid invoices",
    "expiring": "warranties expiring soon",
    "vendor_chart": "spend per vendor",
    "category_chart": "asset value per category",
    "renewals": "upcoming services and AMC renewals",
    "expiry_window": "how many days ahead counts as expiring soon",
    "new_purchase": "button to record a purchase and its invoice",
    "invoice_upload": "invoice PDF/photo upload on the purchase form",
    "purchase_filters": "search and filter purchases: vendor, payment, dates, missing invoice",
    "register_asset": "button to register an asset",
    "warranty_fields": "warranty start and months on the asset form; expiry is calculated",
    "amc_fields": "AMC provider and dates on the asset form",
    "asset_search": "search assets by name, serial no, department, location",
    "warranty_tabs": "warranty status tabs: active, expiring soon, expired, pending",
    "add_service": "button to log a service or repair",
    "new_vendor": "button to add a vendor",
    "report_tabs": "report types",
    "export": "CSV and PDF export buttons",
    "users": "add users and reset passwords (admins only)",
    "sections": "the page menu",
    "sign_out": "log out",
}
SECTIONS = {"dashboard": "dashboard", "purchases": "purchases", "vendors": "vendors", "assets": "assets",
            "warranties": "warranties", "services": "services", "reports": "reports", "users": "users"}


def _fn(name, description, properties=None, required=()):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": list(required)}}}


# Every word here is resent on each step, so descriptions stay terse.
_S = {"type": "string"}
_SHOW = {"type": "boolean", "description": "open the page too (default true)"}
_DATE = {"type": "string", "description": "YYYY-MM-DD"}
YAPPR_TOOLS = [
    _fn("get_summary", "Dashboard numbers: purchases, spend, assets, warranty counts, items needing action, "
        "top vendors, categories, warranties expiring soon, services and AMCs due.",
        {"days": {"type": "integer", "description": "expiring-soon window in days, default 30"}}),
    _fn("find_assets", "List assets; filters and shows the Assets (or Warranties) page.",
        {"query": {"type": "string", "description": "name, serial no, department or location"},
         "category": {"type": "string", "enum": CATEGORIES}, "warranty": {"type": "string", "enum": WARRANTY_STATUSES},
         "status": {"type": "string", "enum": ASSET_STATUSES}, "limit": {"type": "integer"}, "show": _SHOW}),
    _fn("find_purchases", "List purchases/invoices; filters and shows the Purchases page.",
        {"query": {"type": "string", "description": "invoice no, vendor or notes"}, "vendor": _S,
         "payment": {"type": "string", "enum": PAYMENT_STATUSES + ["unpaid"]},
         "missing_invoice": {"type": "boolean"}, "date_from": _DATE, "date_to": _DATE,
         "limit": {"type": "integer"}, "show": _SHOW}),
    _fn("open_asset", "Open an asset: warranty, AMC, invoice and service history.",
        {"asset_id": {"type": "integer"}, "serial_no": _S, "name": _S}),
    _fn("open_purchase", "Open a purchase: invoice details and its assets.",
        {"purchase_id": {"type": "integer"}, "invoice_no": _S}),
    _fn("find_services", "List service/repair records; shows the Services page.",
        {"query": {"type": "string", "description": "asset, serial, issue or provider"},
         "status": {"type": "string", "enum": SERVICE_STATUSES}, "limit": {"type": "integer"}, "show": _SHOW}),
    _fn("find_vendors", "List vendors with purchase count and spend; shows Vendors.", {"query": _S, "show": _SHOW}),
    _fn("get_report", "Run a report and show it; format csv/pdf also gives a download link.",
        {"type": {"type": "string", "enum": list(REPORTS)}, "vendor": _S,
         "payment": {"type": "string", "enum": PAYMENT_STATUSES + ["unpaid"]},
         "category": {"type": "string", "enum": CATEGORIES}, "warranty": {"type": "string", "enum": WARRANTY_STATUSES},
         "status": {"type": "string", "enum": SERVICE_STATUSES}, "date_from": _DATE, "date_to": _DATE,
         "format": {"type": "string", "enum": ["csv", "pdf"]}}, ["type"]),
    _fn("navigate", "Open a page.", {"section": {"type": "string", "enum": list(SECTIONS)}}, ["section"]),
    _fn("point_at", "Show where something is: the companion flies to it, rings it and says text. "
        "For how/where questions a tool can't just do.",
        {"target": {"type": "string", "enum": list(POINT_TARGETS)},
         "text": {"type": "string", "description": "one friendly sentence, under 25 words: what to do there"}},
        ["target", "text"]),
]
# Guide mode ("how do I / where is…") offers point_at alone, with a glossary of what each spot shows.
_GLOSSARY = "; ".join(f"{k}={v}" for k, v in POINT_TARGETS.items())
YAPPR_GUIDE_TOOLS = [_fn("point_at", f"Show where something is on the page and what to do there. Targets: {_GLOSSARY}",
                         YAPPR_TOOLS[-1]["function"]["parameters"]["properties"], ["target", "text"])]

YAPPR_SYSTEM = f"""You are Yappr, the friendly assistant inside {APP_NAME}, the app this business uses to track purchases, invoices, vendors, assets, warranties, AMCs and service/repair history. If asked your name, you're Yappr. You appear as a little animated companion on the page.
Facts only from tool results; never guess numbers, names, dates or amounts. To show, open, filter or export, use the tools (the page follows them), then say in one line what is on screen. A how, where or show-me question always gets point_at (or the tool that does it), never a text-only answer. Use as few tool calls as the question needs.
Warranty status is Active, Expiring Soon (within the window, default {EXPIRING_DAYS} days), Expired, or Pending (no coverage recorded). Amounts are Indian rupees: write ₹1,23,456.
Sound like a warm colleague: the answer first, then at most three short points. Plain sentences, no tables or headings. Reply in the user's language (English, Hindi or Hinglish)."""


def _iso(d):
    return d.isoformat() if d else None


def _limit(n, default=10, top=25):
    try:
        return max(1, min(int(n), top))
    except (TypeError, ValueError):
        return default


def _asset_brief(a):
    return {"asset_id": a.id, "name": a.name, "serial_no": a.serial_no, "category": a.category,
            "department": a.department, "location": a.location, "status": a.status, "price": a.price,
            "warranty_expiry": _iso(a.warranty_expiry), "warranty": a.warranty_status, "days_left": a.days_left,
            "amc_end": _iso(a.amc_end), "vendor": a.purchase.vendor.name if a.purchase else None,
            "invoice_no": a.purchase.invoice_no if a.purchase else None}


def _purchase_brief(p):
    return {"purchase_id": p.id, "invoice_no": p.invoice_no, "date": _iso(p.purchase_date), "vendor": p.vendor.name,
            "amount": p.amount, "tax": p.tax, "total": p.total, "payment": p.payment_status,
            "invoice_document": bool(p.invoice_file), "assets": len(p.assets)}


def _service_brief(s):
    return {"service_id": s.id, "date": _iso(s.service_date), "asset": s.asset.name, "asset_id": s.asset_id,
            "issue": s.issue, "provider": s.provider, "cost": s.cost, "action_taken": s.action_taken,
            "status": s.status, "next_service": _iso(s.next_service_date)}


def _go(endpoint, label, **args):
    return {"go": url_for(endpoint, **{k: v for k, v in args.items() if v not in (None, "")}), "label": label}


def _vendor_id(name):
    v = Vendor.query.filter(Vendor.name.ilike(f"%{name}%")).first() if name else None
    return v.id if v else None


def t_get_summary(days=None, **_):
    d = dashboard_data(_limit(days, EXPIRING_DAYS, 365))
    return {
        "expiring_window_days": d["days"], "purchases": d["purchase_count"], "total_spend": d["total_spend"],
        "assets": d["asset_count"], "asset_value": d["asset_value"], "warranties": d["counts"],
        "needs_attention": {label: n for label, (n, _url) in d["alerts"].items()},
        "top_vendors": [{"vendor": n, "purchases": c, "spend": t} for n, c, t, _p in d["by_vendor"]],
        "by_category": [{"category": n, "assets": c, "value": t} for n, c, t, _p in d["by_category"]],
        "expiring_soon": [_asset_brief(a) for a in d["expiring"]],
        "services_due": [_service_brief(s) for s in d["services_due"]],
        "amc_ending": [{"asset": a.name, "asset_id": a.id, "amc_end": _iso(a.amc_end)} for a in d["amc_expiring"]],
    }, []


def t_find_assets(query="", category=None, warranty=None, status=None, limit=10, show=True, **_):
    args = {k: v for k, v in dict(q=str(query or "").strip(), category=category, warranty=warranty,
                                  status=status).items() if v}
    items = asset_query(MultiDict(args)).all()
    acts = []
    if show is not False:
        acts = [_go("warranties", f"Opened Warranties · {warranty}", status=warranty) if set(args) == {"warranty"}
                else _go("assets", "Filtered assets" if args else "Opened Assets", **args)]
    return {"found": len(items), "assets": [_asset_brief(a) for a in items[:_limit(limit)]]}, acts


def t_find_purchases(query="", vendor=None, payment=None, missing_invoice=False, date_from=None, date_to=None,
                     limit=10, show=True, **_):
    vid = _vendor_id(vendor)
    if vendor and not vid:
        return {"found": 0, "note": f"No vendor matches “{vendor}”."}, []
    args = {k: v for k, v in dict(q=str(query or "").strip(), vendor_id=vid, payment=payment,
                                  missing=1 if missing_invoice else None, date_from=date_from,
                                  date_to=date_to).items() if v}
    items = purchase_query(MultiDict(args)).all()
    acts = [_go("purchases", "Filtered purchases" if args else "Opened Purchases", **args)] if show is not False else []
    return {"found": len(items), "total_value": sum((p.total for p in items), Decimal(0)),
            "purchases": [_purchase_brief(p) for p in items[:_limit(limit)]]}, acts


def t_open_asset(asset_id=None, serial_no=None, name=None, **_):
    a = (db.session.get(Asset, _limit(asset_id, 0, 10 ** 9)) if asset_id else None) \
        or (Asset.query.filter(Asset.serial_no.ilike(str(serial_no).strip())).first() if serial_no else None) \
        or (Asset.query.filter(Asset.name.ilike(f"%{name}%")).first() if name else None)
    if not a:
        return {"error": "No such asset. Use find_assets to get IDs."}, []
    return {**_asset_brief(a), "warranty_start": _iso(a.warranty_start), "warranty_months": a.warranty_months,
            "coverage": a.coverage_type, "amc_provider": a.amc_provider, "amc_start": _iso(a.amc_start),
            "invoice_document": bool(a.purchase and a.purchase.invoice_file),
            "services": [_service_brief(s) for s in a.services[:10]]}, [_go("asset_detail", "Opened the asset", id=a.id)]


def t_open_purchase(purchase_id=None, invoice_no=None, **_):
    p = (db.session.get(Purchase, _limit(purchase_id, 0, 10 ** 9)) if purchase_id else None) \
        or (Purchase.query.filter(Purchase.invoice_no.ilike(str(invoice_no).strip())).first() if invoice_no else None)
    if not p:
        return {"error": "No such purchase. Use find_purchases to get IDs."}, []
    return {**_purchase_brief(p), "notes": p.notes, "gstin": p.vendor.gstin,
            "asset_list": [_asset_brief(a) for a in p.assets]}, [_go("purchase_detail", "Opened the invoice", id=p.id)]


def t_find_services(query="", status=None, limit=10, show=True, **_):
    args = {k: v for k, v in dict(q=str(query or "").strip(), status=status).items() if v}
    items = service_query(MultiDict(args)).all()
    acts = [_go("services", "Filtered services" if args else "Opened Services", **args)] if show is not False else []
    return {"found": len(items), "total_cost": sum((s.cost for s in items), Decimal(0)),
            "services": [_service_brief(s) for s in items[:_limit(limit)]]}, acts


def t_find_vendors(query="", show=True, **_):
    q = str(query or "").strip()
    items = vendor_query(q).all()
    acts = [_go("vendors", "Opened Vendors", q=q)] if show is not False else []
    return {"found": len(items), "vendors": [{"vendor": v.name, "contact": v.contact_person, "phone": v.phone,
                                              "gstin": v.gstin, "purchases": len(v.purchases),
                                              "spend": v.total_spend} for v in items[:25]]}, acts


def t_get_report(type="purchases", vendor=None, format=None, **filters):
    kind = type if type in REPORTS else "purchases"
    keep = ("payment", "category", "warranty", "status", "date_from", "date_to")
    args = {k: v for k, v in filters.items() if k in keep and v} | {"type": kind}
    if vendor and (vid := _vendor_id(vendor)):
        args["vendor_id"] = vid
    headers, rows, totals = build_report(kind, MultiDict(args))
    acts = [_go("reports", f"Opened {REPORTS[kind]}", **args)]
    if format in ("csv", "pdf"):
        acts.append({"link": url_for("reports", format=format, **args), "label": f"Download {format.upper()}"})
    return {"report": REPORTS[kind], "rows": len(rows), "first_rows": [dict(zip(headers, r)) for r in rows[:12]],
            "totals": dict(zip(headers, totals))}, acts


def t_navigate(section=None, **_):
    if section not in SECTIONS or (section == "users" and g.user.role != "admin"):
        return {"error": f"section is one of {', '.join(s for s in SECTIONS if s != 'users')}"}, []
    return {"now_showing": section}, [_go(SECTIONS[section], f"Opened {section.capitalize()}")]


def t_point_at(target=None, text="", **_):
    if target not in POINT_TARGETS or (target == "users" and g.user.role != "admin"):
        return {"error": f"target is one of {', '.join(POINT_TARGETS)}"}, []
    return {"pointing_at": target}, [{"point": target, "text": str(text or "Right here.")[:240]}]


YAPPR_IMPL = {"get_summary": t_get_summary, "find_assets": t_find_assets, "find_purchases": t_find_purchases,
              "open_asset": t_open_asset, "open_purchase": t_open_purchase, "find_services": t_find_services,
              "find_vendors": t_find_vendors, "get_report": t_get_report, "navigate": t_navigate,
              "point_at": t_point_at}


def yappr_clean(messages):
    """Only the fields the model needs, so the page can't smuggle in a new system prompt."""
    if not isinstance(messages, list):
        return None
    text = lambda s: str(s if s is not None else "")[:12000]
    sig = lambda x: ({"extra_content": {"google": {"thought_signature": x["extra_content"]["google"]["thought_signature"][:20000]}}}
                     if isinstance(((x.get("extra_content") or {}).get("google") or {}).get("thought_signature"), str) else {})
    out = []
    for m in messages[-30:]:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user":
            out.append({"role": "user", "content": text(m.get("content"))})
        elif m.get("role") == "tool":
            out.append({"role": "tool", "tool_call_id": text(m.get("tool_call_id"))[:200], "content": text(m.get("content"))})
        elif m.get("role") == "assistant":
            calls = [{"id": text(c.get("id"))[:200], "type": "function",
                      "function": {"name": text((c.get("function") or {}).get("name"))[:64],
                                   "arguments": text((c.get("function") or {}).get("arguments") or "{}")}, **sig(c)}
                     for c in (m.get("tool_calls") or [])[:8] if isinstance(c, dict)]
            out.append({"role": "assistant", "content": None if m.get("content") is None else text(m.get("content")),
                        **({"tool_calls": calls} if calls else {}), **sig(m)})
    while out and out[0]["role"] != "user":  # a history cut mid-exchange starts with orphaned tool results
        out.pop(0)
    return out or None


def _for_gemini(convo):
    out = []
    for m in convo:
        calls = m.get("tool_calls")
        if calls and not any(c.get("extra_content") for c in calls):  # an unsigned batch (a Groq turn)
            calls = [{**calls[0], "extra_content": {"google": {"thought_signature": SKIP_SIG}}}, *calls[1:]]
            m = {**m, "tool_calls": calls}
        out.append(m)
    return out


def _for_groq(convo):
    strip = lambda d: {k: v for k, v in d.items() if k != "extra_content"}
    return [strip({**m, "tool_calls": [strip(c) for c in m["tool_calls"]]} if m.get("tool_calls") else m) for m in convo]


def _model_post(url, key, body):
    # Groq's Cloudflare front refuses urllib's default "Python-urllib" agent (error 1010).
    req = urllib.request.Request(url, json.dumps(body, default=str).encode(),
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                  "User-Agent": f"{APP_NAME}-Yappr/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)["choices"][0]["message"]


def yappr_complete(convo, tools, choice, guide):
    """One model step: each candidate in turn until one answers. Returns (message, failure)."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    groq_keys = [k for k in (os.environ.get("GROQ_API_KEY", "").strip(), os.environ.get("GROQ_API_KEY_2", "").strip()) if k]
    tries = [("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", gemini_key,
              {"model": m, "messages": _for_gemini(convo), "tools": tools, "tool_choice": choice,
               "reasoning_effort": "minimal" if "lite" in m else "low", "max_completion_tokens": 300 if guide else 800})
             for m in (GEMINI_MODELS if gemini_key else [])]
    tries += [("https://api.groq.com/openai/v1/chat/completions", k,
               {"model": GROQ_MODEL, "messages": _for_groq(convo), "tools": tools, "tool_choice": choice,
                "temperature": 0.2, "reasoning_effort": "none", "max_completion_tokens": 200 if guide else 450})
              for k in groq_keys]
    failure = ""
    for url, key, body in tries:
        try:
            m = _model_post(url, key, body)
        except urllib.error.HTTPError as e:
            failure = f"{e.code} {e.read()[:200]!r}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as e:
            failure = str(e)
        else:
            msg = {"role": "assistant", "content": m.get("content")}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{**c, "id": c.get("id") or f"call_{i}"} for i, c in enumerate(m["tool_calls"])]
            if m.get("extra_content"):
                msg["extra_content"] = m["extra_content"]
            return msg, ""
        app.logger.warning("yappr model failed: %s", failure[:200])
    return None, failure


@app.post("/yappr/chat")
def yappr_chat():
    if len(request.get_data()) > 400_000:
        return {"error": "That conversation is too long. Start a new one."}, 413
    body = request.get_json(silent=True) or {}
    messages = yappr_clean(body.get("messages"))
    if not messages:
        return {"error": "Nothing to answer."}, 400
    if not (os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GROQ_API_KEY", "").strip()):
        return {"error": YAPPR_NO_KEY}, 503
    last = max(i for i, m in enumerate(messages) if m["role"] == "user")
    now = datetime.now().strftime("%A %d %B %Y, %I:%M %p")
    messages[last] = {**messages[last], "content": f"{messages[last]['content']}\n[It is {now}. User: {g.user.username} ({g.user.role})]"}
    guide = body.get("mode") == "guide"
    convo = [{"role": "system", "content": YAPPR_SYSTEM}, *messages]
    new, actions = [], []
    for step in range(6):
        msg, failure = yappr_complete(convo, YAPPR_GUIDE_TOOLS if guide else YAPPR_TOOLS,
                                      "required" if guide and step == 0 else "auto", guide)
        if msg is None:
            busy = any(w in failure.lower() for w in ("429", "rate", "quota", "busy", "overload"))
            return {"error": "Yappr is busy for a moment. Ask again in a few seconds." if busy
                    else "Yappr couldn’t get an answer just now. Try again in a moment.", "messages": new}, 502
        convo.append(msg)
        new.append(msg)
        if not msg.get("tool_calls"):
            break
        shown = ""
        for c in msg["tool_calls"]:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
                args = args if isinstance(args, dict) else {}
                out, acts = YAPPR_IMPL[name](**args) if name in YAPPR_IMPL else ({"error": f"no tool {name}"}, [])
            except Exception as e:  # a bad argument is the model's to fix, not a 500
                db.session.rollback()
                out, acts = {"error": str(e)[:300]}, []
            actions += [{**a, "tool": name} for a in acts]
            if name == "point_at" and "error" not in out:
                shown = acts[0]["text"]
            tool_msg = {"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, default=str)[:8000]}
            convo.append(tool_msg)
            new.append(tool_msg)
        # A point already says what to do there: no second model trip just to repeat it.
        if shown:
            new.append({"role": "assistant", "content": shown})
            break
    return {"messages": new, "actions": actions}


# ---------------------------------------------------------------- setup & demo data

with app.app_context():
    db.create_all()
    if not User.query.first():
        db.session.add(User(username="admin", password_hash=generate_password_hash(DEFAULT_ADMIN_PASSWORD), role="admin"))
        db.session.commit()


@app.cli.command("seed")
def seed():
    """Load demo vendors, purchases, assets and service records."""
    if Vendor.query.first():
        print("Data already present; seed skipped.")
        return
    t = date.today()
    ago = lambda n: t - timedelta(days=n)
    tw = Vendor(name="TechWorld Solutions", contact_person="Rakesh Shah", phone="9820012345",
                email="sales@techworld.example", address="Andheri East, Mumbai", gstin="27AABCT1234F1Z5")
    ca = Vendor(name="CoolAir Services", contact_person="Meena Iyer", phone="9930054321",
                email="support@coolair.example", address="Thane West, Thane", gstin="27AADCC5678K1Z2")
    nl = Vendor(name="NetLink Systems", contact_person="Imran Khan", phone="9867011223",
                email="orders@netlink.example", address="Vashi, Navi Mumbai", gstin="27AAFCN9012L1Z9")
    oc = Vendor(name="OfficeCraft Furniture", contact_person="Sunita Patil", phone="9821098765",
                email="hello@officecraft.example", address="Kurla West, Mumbai")
    p1 = Purchase(vendor=tw, invoice_no="TW/2024/1021", purchase_date=ago(700), amount=Decimal("186000"),
                  tax=Decimal("33480"), payment_status="Paid", notes="Laptops for design team")
    p2 = Purchase(vendor=ca, invoice_no="CA-118", purchase_date=ago(900), amount=Decimal("96000"),
                  tax=Decimal("26880"), payment_status="Paid")
    p3 = Purchase(vendor=nl, invoice_no="NL-5567", purchase_date=ago(200), amount=Decimal("72500"),
                  tax=Decimal("13050"), payment_status="Partial", notes="Office network upgrade")
    p4 = Purchase(vendor=oc, invoice_no="OC-889", purchase_date=ago(45), amount=Decimal("54000"),
                  tax=Decimal("9720"), payment_status="Pending")
    p5 = Purchase(vendor=tw, invoice_no="TW/2025/1102", purchase_date=ago(400), amount=Decimal("28500"),
                  tax=Decimal("5130"), payment_status="Paid")

    def asset(purchase, months, **kw):
        start = purchase.purchase_date
        return Asset(purchase=purchase, warranty_start=start if months else None, warranty_months=months,
                     warranty_expiry=add_months(start, months) if months else None, **kw)

    laptops = [asset(p1, 24, name=f"Dell Latitude 5440 #{i}", category="Laptop", serial_no=f"DL5440-A0{i}",
                     price=Decimal("62000"), department="Design", location="2nd floor",
                     coverage_type="Onsite, parts & labour") for i in (1, 2, 3)]
    acs = [asset(p2, 12, name=f"Daikin 1.5T Split AC #{i}", category="Air Conditioner", serial_no=f"DK15-77{i}",
                 price=Decimal("48000"), department="Admin", location=f"Conference room {i}",
                 coverage_type="Compressor & parts", amc_provider="CoolAir Services",
                 amc_start=ago(340), amc_end=ago(340) + timedelta(days=365)) for i in (1, 2)]
    router = asset(p3, 36, name="Cisco ISR 1100 Router", category="Networking", serial_no="FGL2231X0AB",
                   price=Decimal("42500"), department="IT", location="Server room", coverage_type="Replacement")
    switch = asset(p3, 60, name="Netgear 24-port PoE Switch", category="Networking", serial_no="NG24P-3390",
                   price=Decimal("30000"), department="IT", location="Server room", coverage_type="Lifetime limited")
    chairs = asset(p4, 12, name="Ergonomic chairs (set of 12)", category="Furniture", price=Decimal("54000"),
                   department="Operations", location="Open office", coverage_type="Manufacturing defects")
    printer = asset(p5, None, name="HP LaserJet Pro M404dn", category="Printer", serial_no="PHBQ8123",
                    price=Decimal("28500"), department="Accounts", location="1st floor")
    db.session.add_all([tw, ca, nl, oc, p1, p2, p3, p4, p5, *laptops, *acs, router, switch, chairs, printer])
    db.session.add_all([
        ServiceRecord(asset=acs[0], service_date=ago(75), issue="Low cooling", provider="CoolAir Services",
                      cost=Decimal("3500"), action_taken="Gas refill and filter cleaning",
                      next_service_date=t + timedelta(days=15), status="Resolved"),
        ServiceRecord(asset=printer, service_date=ago(120), issue="Frequent paper jams", provider="HP Care",
                      cost=Decimal("1800"), action_taken="Pickup roller replaced", status="Resolved"),
        ServiceRecord(asset=laptops[1], service_date=ago(4), issue="Keyboard keys not responding",
                      provider="TechWorld Solutions", cost=Decimal("0"), action_taken="Sent for warranty repair",
                      status="Open"),
    ])
    laptops[1].status = "Under Repair"
    db.session.commit()
    print("Demo data loaded.")


if __name__ == "__main__":
    app.run(debug=True)
