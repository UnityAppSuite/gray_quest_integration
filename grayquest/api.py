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

        # Build return URLs
        payment_hash = frappe.db.get_value("Payment Request", payment_request, "payment_hash")
        base_url = frappe.utils.get_url()
        if payment_hash:
            success_url = f"{base_url}/tgaa-connect/payment-status?status=success&payment_request={payment_hash}"
            failure_url = f"{base_url}/tgaa-connect/payment-status?status=failure&payment_request={payment_hash}"
        else:
            success_url = failure_url = "/"

        # Skip if already paid
        if frappe.db.get_value("Payment Request", payment_request, "status") == "Paid":
            frappe.local.response["type"] = "redirect"
            frappe.local.response["location"] = success_url
            return

        # Security: Require application_code to prevent URL tampering
        # GrayQuest always sends application_code on successful payment
        payment_succeeded = False
        if status == "success" and application_code:
            frappe.db.set_value("Payment Request", payment_request, "transaction_id", application_code)
            doc = frappe.get_doc("Payment Request", payment_request)

            # Check if this is Student Applicant - handle one-time fee payment
            if doc.reference_doctype == "Student Applicant":
                _handle_student_applicant_one_time_fee_callback(doc, application_code)
            else:
                # Standard flow for other reference doctypes
                doc.on_payment_authorized(status="Completed")

            frappe.db.commit()
            payment_succeeded = True

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = success_url if payment_succeeded else failure_url

    except Exception as e:
        frappe.log_error(title="GrayQuest Callback Error", message=frappe.get_traceback())
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "/"


def _handle_student_applicant_one_time_fee_callback(pr_doc, transaction_id: str):
    """
    Handle one-time fee payment for Student Applicant via callback.

    Checks if the payment is for one-time fee (amount matches one_time_fee_amount)
    and routes to validate_one_time_payment. Otherwise, uses standard flow.

    Args:
        pr_doc: Payment Request document
        transaction_id (str): GrayQuest transaction ID (application_code)
    """
    from frappe.utils import flt

    # Get Student Applicant document
    applicant = frappe.get_doc("Student Applicant", pr_doc.reference_name)
    payment_amount = flt(pr_doc.grand_total)
    one_time_fee = flt(getattr(applicant, 'one_time_fee_amount', 0))

    # Prepare payment data
    payment_data = {
        "amount": payment_amount,
        "transaction_id": transaction_id,
    }

    # Check if this is one-time fee payment (amount matches one_time_fee_amount)
    if one_time_fee > 0 and payment_amount == one_time_fee:
        # One-time fee payment - call validate_one_time_payment
        if hasattr(applicant, 'validate_one_time_payment'):
            applicant.validate_one_time_payment(data=payment_data, payment_mode="Online")
        else:
            # Fallback to standard flow if method doesn't exist
            pr_doc.on_payment_authorized(status="Completed")
    else:
        # Standard flow for other Student Applicant payments (not one-time fee)
        pr_doc.on_payment_authorized(status="Completed")
