import frappe
from frappe import _


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook_handler(**kwargs):
    """Handle GrayQuest webhook notifications."""
    data = frappe.parse_json(kwargs)
    controller = frappe.get_last_doc("GrayQuest Settings")
    controller.handle_webhook(data)


@frappe.whitelist()
def check_payment_status(payment_request):
    """Check payment status via GrayQuest API."""
    controller = frappe.get_last_doc("GrayQuest Settings")
    controller.check_payment_status(payment_request)


@frappe.whitelist(allow_guest=True)
def handle_payment_callback(**kwargs):
    """
    Handle the browser redirect from GrayQuest after payment.

    UI-only. The Payment Entry is created exclusively by the webhook
    (`dt.payment.captured` / `emi.disbursed`), which carries the authoritative
    settled amount. The browser redirect only carries `payment_request`,
    `status`, and `application_code` — not the amount — so creating a PE here
    would force us to use PR.grand_total, which can drift from what the
    gateway actually charged (in-session concessions, late-fee waivers,
    partial-payment split rebalancing). That drift was the root cause of the
    historical PE-vs-webhook gap audited in
    `audits/2026-05-20-grayquest/webhook_lt_pe.csv`.

    What this handler does:
      1. Persist `application_code` as `transaction_id` on the PR so the
         webhook can correlate (and so the status page has something to
         display while waiting).
      2. Redirect the parent to the payment status page. That page reads
         `PR.status`, which will flip to "Paid" when the webhook submits
         the PE. Until then it shows the in-flight state.

    GrayQuest passes: payment_request, status, application_code.
    Note: GrayQuest appends params with '?' instead of '&'.
    """
    try:
        payment_request = kwargs.get("payment_request")
        status = kwargs.get("status", "").lower()
        application_code = kwargs.get("application_code")

        # Clean payment_request (GrayQuest appends ?entity=direct)
        if payment_request and "?" in payment_request:
            payment_request = payment_request.split("?")[0]

        # Validate payment_request exists
        if not payment_request or not frappe.db.exists("Payment Request", payment_request):
            frappe.local.response["type"] = "redirect"
            frappe.local.response["location"] = "/"
            return

        # Build return URL
        payment_hash = frappe.db.get_value("Payment Request", payment_request, "payment_hash")
        return_url = f"/payment?payment_request={payment_hash}" if payment_hash else "/"

        # Persist the application_code on the PR so the webhook can correlate
        # even if it arrives after the parent has landed on the status page.
        # No PE work happens here.
        if status == "success" and application_code:
            frappe.db.set_value(
                "Payment Request", payment_request, "transaction_id", application_code
            )
            frappe.db.commit()

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = return_url

    except Exception:
        frappe.log_error(title="GrayQuest Callback Error", message=frappe.get_traceback())
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "/"
