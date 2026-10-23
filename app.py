import os, json, hmac, hashlib, secrets
from datetime import datetime
from decimal import Decimal
from functools import wraps

import mysql.connector
from mysql.connector import pooling
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, abort
import razorpay

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "CHANGE_ME_TO_A_LONG_RANDOM_SECRET")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                 SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true")

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "bitebox"),
    "port": int(os.environ.get("DB_PORT", "3306")),
}
pool = None
def get_pool():
    global pool
    if pool is None:
        pool = pooling.MySQLConnectionPool(pool_name="bitebox_pool", pool_size=5, **DB_CONFIG)
    return pool

def db():
    return get_pool().get_connection()

def gateway():
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        return None
    return razorpay.Client(auth=(key_id, key_secret))

MENU = [
    {"id": 1, "name": "Margherita Pizza", "category": "Pizza", "price": 19900, "emoji": "🍕", "description": "Classic cheese and tomato"},
    {"id": 2, "name": "Veg Burger", "category": "Burgers", "price": 9900, "emoji": "🍔", "description": "Crispy, fresh and satisfying"},
    {"id": 3, "name": "Chicken Biryani", "category": "Indian", "price": 22000, "emoji": "🍛", "description": "Fragrant basmati rice and spices"},
    {"id": 4, "name": "French Fries", "category": "Sides", "price": 7900, "emoji": "🍟", "description": "Golden and crispy"},
    {"id": 5, "name": "Paneer Wrap", "category": "Indian", "price": 14900, "emoji": "🌯", "description": "Paneer with crunchy vegetables"},
    {"id": 6, "name": "Chocolate Brownie", "category": "Desserts", "price": 8900, "emoji": "🍰", "description": "Rich chocolate brownie"},
]
MENU_BY_ID = {x["id"]: x for x in MENU}

def money(paise):
    return f"₹{paise / 100:.2f}"

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper

@app.get("/")
def index():
    return render_template("index.html", menu=MENU, razorpay_key_id=os.environ.get("RAZORPAY_KEY_ID", ""))

@app.get("/health")
def health():
    return jsonify({"status": "ok"})

@app.post("/api/create-order")
def create_order():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()[:100]
    email = str(data.get("email", "")).strip()[:150]
    phone = str(data.get("phone", "")).strip()[:20]
    address = str(data.get("address", "")).strip()[:255]
    items = data.get("items", [])
    if not name or not email or "@" not in email or not address or not isinstance(items, list) or not items:
        return jsonify(error="Please provide valid customer details and at least one item."), 400

    quantities = {}
    try:
        for item in items:
            fid, qty = int(item["id"]), int(item["quantity"])
            if fid not in MENU_BY_ID or qty < 1 or qty > 30:
                raise ValueError()
            quantities[fid] = quantities.get(fid, 0) + qty
        if any(q > 30 for q in quantities.values()):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Invalid menu items or quantities."), 400

    total = sum(MENU_BY_ID[fid]["price"] * qty for fid, qty in quantities.items())
    client = gateway()
    if client is None:
        return jsonify(error="Online payment is not configured yet. Add Razorpay keys to the server environment first."), 503

    conn = db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO customers (name,email,phone,address) VALUES (%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE name=VALUES(name), phone=VALUES(phone), address=VALUES(address)",
                    (name,email,phone,address))
        cur.execute("SELECT customer_id FROM customers WHERE email=%s", (email,))
        customer_id = cur.fetchone()[0]
        cur.execute("INSERT INTO orders (customer_id,total_paise,status,payment_status) VALUES (%s,%s,'Pending','Created')",
                    (customer_id,total))
        order_id = cur.lastrowid
        for fid, qty in quantities.items():
            food = MENU_BY_ID[fid]
            cur.execute("INSERT INTO order_details (order_id,food_id,quantity,unit_price_paise) VALUES (%s,%s,%s,%s)",
                        (order_id,fid,qty,food["price"]))
        receipt = f"BB{order_id}-{secrets.token_hex(3)}"
        r_order = client.order.create(data={"amount": total, "currency": "INR", "receipt": receipt,
                                           "notes": {"internal_order_id": str(order_id)}})
        cur.execute("UPDATE orders SET razorpay_order_id=%s WHERE order_id=%s", (r_order["id"],order_id))
        cur.execute("INSERT INTO payments (order_id,amount_paise,method,status,razorpay_order_id) VALUES (%s,%s,'Razorpay','Created',%s)",
                    (order_id,total,r_order["id"]))
        conn.commit()
        session["last_order_id"] = order_id
        return jsonify(order_id=order_id, razorpay_order_id=r_order["id"], amount=total,
                       currency="INR", key_id=os.environ["RAZORPAY_KEY_ID"],
                       customer={"name":name,"email":email,"contact":phone},
                       description=f"BiteBox order #{order_id}")
    except Exception:
        conn.rollback()
        app.logger.exception("Could not create order")
        return jsonify(error="Could not create the order. Please try again."), 500
    finally:
        cur.close()
        conn.close()

@app.post("/api/verify-payment")
def verify_payment():
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id")
    payment_id = str(data.get("razorpay_payment_id", ""))
    razorpay_order_id = str(data.get("razorpay_order_id", ""))
    signature = str(data.get("razorpay_signature", ""))
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not all([order_id, payment_id, razorpay_order_id, signature, secret]):
        return jsonify(error="Payment verification details are missing."), 400

    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM orders WHERE order_id=%s AND razorpay_order_id=%s", (order_id,razorpay_order_id))
        order = cur.fetchone()
        if not order:
            return jsonify(error="Order not found."), 404
        expected = hmac.new(secret.encode(), f"{razorpay_order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return jsonify(error="Payment signature verification failed."), 400

        client = gateway()
        payment = client.payment.fetch(payment_id)
        if payment.get("order_id") != razorpay_order_id or int(payment.get("amount", -1)) != int(order["total_paise"]):
            return jsonify(error="Payment details do not match this order."), 400
        # Razorpay can return authorized before capture; only mark paid after captured.
        if payment.get("status") != "captured":
            return jsonify(error="Payment is not captured yet. If money was debited, contact support with your order number."), 409
        cur.execute("UPDATE orders SET payment_status='Paid',status='Confirmed',razorpay_payment_id=%s WHERE order_id=%s",
                    (payment_id,order_id))
        cur.execute("UPDATE payments SET status='Paid',razorpay_payment_id=%s WHERE order_id=%s",
                    (payment_id,order_id))
        conn.commit()
        return jsonify(success=True, order_id=order_id, status="Confirmed")
    except Exception:
        conn.rollback()
        app.logger.exception("Payment verification failed")
        return jsonify(error="Could not verify payment. Please contact the site administrator."), 500
    finally:
        cur.close()
        conn.close()

@app.get("/order/<int:order_id>")
def order_confirmation(order_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""SELECT o.order_id,o.total_paise,o.status,o.payment_status,o.created_at,
                              c.name,c.email,c.address
                       FROM orders o JOIN customers c ON c.customer_id=o.customer_id
                       WHERE o.order_id=%s""", (order_id,))
        order = cur.fetchone()
        if not order:
            abort(404)
        cur.execute("""SELECT f.food_name,d.quantity,d.unit_price_paise
                       FROM order_details d JOIN food_items f ON f.food_id=d.food_id
                       WHERE d.order_id=%s""", (order_id,))
        items = cur.fetchall()
        return render_template("confirmation.html", order=order, items=items, money=money)
    finally:
        cur.close()
        conn.close()

@app.get("/admin/login")
def admin_login():
    return render_template("admin_login.html")

@app.post("/admin/login")
def admin_login_post():
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        return render_template("admin_login.html", error="ADMIN_PASSWORD is not configured on the server."), 503
    submitted = request.form.get("password", "")
    if hmac.compare_digest(submitted, password):
        session["is_admin"] = True
        return redirect(url_for("admin_orders"))
    return render_template("admin_login.html", error="Incorrect password."), 401

@app.post("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

@app.get("/admin")
@admin_required
def admin_orders():
    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""SELECT o.order_id,o.total_paise,o.status,o.payment_status,o.created_at,c.name,c.email,c.phone
                       FROM orders o JOIN customers c ON c.customer_id=o.customer_id
                       ORDER BY o.created_at DESC LIMIT 300""")
        orders = cur.fetchall()
        return render_template("admin.html", orders=orders, money=money)
    finally:
        cur.close()
        conn.close()

@app.post("/admin/order/<int:order_id>/status")
@admin_required
def update_status(order_id):
    status = request.form.get("status", "")
    allowed = {"Confirmed","Preparing","Ready","Delivered","Cancelled"}
    if status not in allowed:
        abort(400)
    conn = db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE orders SET status=%s WHERE order_id=%s AND payment_status='Paid'", (status,order_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("admin_orders"))

@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", message="Page or order not found."), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
