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
    Handle redirect from GrayQuest after payment.

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

        # Skip if already paid
        if frappe.db.get_value("Payment Request", payment_request, "status") == "Paid":
            frappe.local.response["type"] = "redirect"
            frappe.local.response["location"] = return_url
            return

        # Security: Require application_code to prevent URL tampering
        # GrayQuest always sends application_code on successful payment
        if status == "success" and application_code:
            frappe.db.set_value("Payment Request", payment_request, "transaction_id", application_code)
            doc = frappe.get_doc("Payment Request", payment_request)
            doc.on_payment_authorized(status="Completed")
            frappe.db.commit()

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = return_url

    except Exception as e:
        frappe.log_error(title="GrayQuest Callback Error", message=frappe.get_traceback())
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "/"
