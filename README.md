# BiteBox — real food ordering starter

Flask backend + MySQL relational database + Razorpay Checkout integration. The starter includes menu/cart, server-calculated totals, order records, payment signature verification, payment status checks, order confirmation, and a password-protected admin order dashboard.

## Important truth about launch readiness
This is an implementation starter, not a live deployed store. It will not accept real payments until you:
1. Create and get approval for a Razorpay merchant account.
2. Add your Razorpay **test** keys as environment variables and complete a test payment.
3. Set up a reachable MySQL service and configure the DB environment variables.
4. Deploy the app to a host that supports Flask/Python and configure HTTPS, environment secrets, database networking and backups.
5. Switch to live Razorpay keys only after merchant verification and end-to-end testing.

Do not put secrets in source code, frontend JavaScript, GitHub, screenshots, or chat. Never collect card/UPI credentials directly. Razorpay Checkout handles those details.

## Local setup
Requires Python 3.11+ and MySQL 8+.

1. Create a database/user in MySQL, then import the schema:
   `mysql -u root -p < schema.sql`
2. Create a virtual environment and install packages:
   `python -m venv .venv`
   - Windows: `.venv\Scripts\activate`
   - macOS/Linux: `source .venv/bin/activate`
   `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill the values. Export variables from `.env` using your preferred local environment tool; this project does not load `.env` automatically.
4. Start: `python app.py`
5. Visit `http://127.0.0.1:5000`

For a quick local secret:
`python -c "import secrets; print(secrets.token_urlsafe(48))"`

## Razorpay test mode
- Create a Razorpay account and use its Dashboard to obtain test API keys.
- Set `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` on the server.
- Test checkout with Razorpay's official test instruments, not real money.
- The backend creates the Razorpay order, computes the amount from trusted server-side menu prices, verifies the checkout signature, fetches the payment from Razorpay and requires the payment status to be `captured` before marking the order Paid.
- Before production, configure Razorpay webhooks for payment events and implement idempotent webhook handling/reconciliation for cases where a customer closes the browser after paying. Do not rely on the browser callback alone for complete production reconciliation.

## Free hosting notes
Free-tier offerings and terms change. Pick a host that supports a Python web service and pair it with a compatible managed MySQL provider. Some free databases sleep, expire, or have connection restrictions; check retention, backups, outbound networking, SSL and monthly limits. A free host is not a promise of uninterrupted production service. Set `COOKIE_SECURE=true` when HTTPS is configured.

## Routes
- `/` — food menu and checkout
- `/order/<id>` — order confirmation
- `/admin/login` — admin login
- `/admin` — admin order management (login required)
- `/health` — health check

## Before accepting customers
Add delivery zones/fees, tax rules, business contact details, privacy notice, terms, cancellation/refund policy, food allergen information, customer support workflow, rate limiting, CSRF protection for form routes, admin login rate limiting, monitoring, database backups, and a Razorpay webhook endpoint. Review all local legal and food-business requirements. The starter uses a small fixed menu for demonstration; update menu prices and inventory to match the actual business.
