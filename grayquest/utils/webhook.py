import frappe
from frappe import _, db, get_doc, response
from frappe.utils import flt, get_datetime, now_datetime

from grayquest.utils import EMI_STATUS_MAPPING


def ensure_mode_of_payment_exists(mode_name):
    """Create Mode of Payment if it doesn't exist"""
    if not frappe.db.exists("Mode of Payment", mode_name):
        frappe.get_doc({
            "doctype": "Mode of Payment",
            "mode_of_payment": mode_name,
            "type": "General"
        }).insert(ignore_permissions=True)


def resolve_payment_request(udf_details):
    """Resolve Payment Request doctype and docname from udf_details.

    Primary: udf_1 (doctype) and udf_2 (docname).
    Fallback: If udf_1/udf_2 is missing or the doc doesn't exist, look up Payment Request
    using udf_3 (fee doctype), udf_4 (fee name), and udf_5 (payment term).

    Returns:
        tuple: (doctype, docname, fee_type) where fee_type is udf_3 value (e.g. "one_time")
    """
    doctype = udf_details.get("udf_1")
    docname = udf_details.get("udf_2")
    fee_type = udf_details.get("udf_3")

    try:
        if doctype and docname and db.exists(doctype, docname):
            return doctype, docname, fee_type
    except Exception:
        pass

    # Fallback: find Payment Request using fee details from udf_3/4/5
    fee_name = udf_details.get("udf_4")
    payment_term = udf_details.get("udf_5")
    if fee_name:
        filters = {
            "reference_doctype": "Fees",
            "reference_name": fee_name,
            "docstatus": 1,
        }
        if payment_term:
            filters["payment_term"] = payment_term
        pr_name = db.get_value("Payment Request", filters, "name", order_by="creation desc")
        if pr_name:
            return "Payment Request", pr_name, fee_type

    return doctype, docname, fee_type


def handle_payment_gateway_webhook(data):
    """
    Handle Payment Gateway Webhook

    Args:
        data (dict): Webhook data

    Details:
        - If event is `dt.payment.order.created`, do nothing
        - If event is `dt.payment.captured`, update application code and call on_payment_authorized
        - the udf_details contains `Payment Request` doctype and docname
        - If fee_type is `one_time`, call validate_one_time_payment on Student Applicant
        - If payment was already processed via callback, just acknowledge the webhook
    """
    if data.get("event") == "dt.payment.captured":
        udf_details = data.get("udf_details", {})
        doctype, docname, fee_type = resolve_payment_request(udf_details)
        # Extract application details from the data
        application_details = data.get("application_details", {})
        # Get application code from application details
        application_code = application_details.get("code")

        # Check if already paid via callback - avoid re-processing
        current_status = db.get_value(doctype, docname, "status") if frappe.db.has_column(doctype, "status") else None
        if current_status == "Paid":
            # Already processed via callback - just acknowledge webhook
            response["message"] = _("Payment already processed via callback")
            return

        # Not yet paid - process via webhook (existing behavior)
        # Fetch the document using doctype and docname
        doc = get_doc(doctype, docname)
        # Get payment details from the data
        payment_details = data.get("payment_details", {})
        amount = payment_details.get("amount")
        # Update the transaction_id and reference_no fields in the document
        if hasattr(doc, "transaction_id"):
            doc.db_set("transaction_id", application_code)
        if hasattr(doc, "paid_amount"):
            doc.db_set("paid_amount", amount)
        # Store bank_reference_id as reference_no on Payment Request
        bank_reference_id = payment_details.get("bank_reference_id")
        if bank_reference_id and hasattr(doc, "reference_no"):
            doc.db_set("reference_no", bank_reference_id)
        # Call the on_payment_authorized method on the document
        if payment_details.get("status") == "PAID":
            # Route based on fee_type for one-time payments
            if fee_type == "one_time" and hasattr(doc, "reference_doctype") and doc.reference_doctype == "Student Applicant":
                # Get Student Applicant and call validate_one_time_payment
                applicant = frappe.get_doc("Student Applicant", doc.reference_name)
                payment_data = {
                    "amount": doc.grand_total,
                    "transaction_id": application_code,
                }
                applicant.validate_one_time_payment(data=payment_data, payment_mode="Online")
                response["message"] = _("One Time Fee Payment Captured")
            elif doc.doctype == "Payment Request":
                # Set mode of payment for GrayQuest PG payments
                ensure_mode_of_payment_exists("GrayQuest")
                doc.db_set("mode_of_payment", "GrayQuest")
                doc.reload()  # Refresh in-memory object for payment_entry()
                doc.on_payment_authorized(status="Completed")
                response["message"] = _("Payment successfully captured and processed.")
            else:
                if hasattr(doc, "validate_payment"):
                    doc.validate_payment(payment_details)
                else:
                    create_payment_entry(doc, amount=amount, transaction_id=application_code)
                response["message"] = _("Payment successfully captured and processed.")

    elif data.get("event") == "dt.payment.order.created":
        udf_details = data.get("udf_details", {})
        doctype, docname, _fee_type = resolve_payment_request(udf_details)
        doc = get_doc(doctype, docname)
        if hasattr(doc, "validate_payment_order_created"):
            res = doc.validate_payment_order_created(data)
            if res:
                response["message"] = res
            else:
                response["message"] = _("Payment order created, awaiting completion.")

    elif data.get("event") == "dt.payment.failed":
        udf_details = data.get("udf_details", {})
        doctype, docname, _fee_type = resolve_payment_request(udf_details)
        doc = get_doc(doctype, docname)
        if hasattr(doc, "validate_failed_payment"):
            res = doc.validate_failed_payment(data)
            if res:
                response["message"] = res
            else:
                response["message"] = _("Payment failed. Please try again or contact support.")


def handle_emi_webhook(data):
    """
    Handle EMI Webhook, and update EMI Status in payment request

    Args:
        data (dict): Webhook data

    Details:
        - the udf_details contains `Payment Request` doctype and docname
        - If udf_details are missing (external webhook not generated by our system),
          attempts to resolve the Payment Request using student_uuid, amount, and
          academic_year from the webhook payload
    """
    udf_details = data.get("udf_details", {})
    doctype, docname, _fee_type = resolve_payment_request(udf_details)

    # Extract application details from the webhook data
    application_details = data.get("application_details", {})
    application_code = application_details.get("code")

    # If udf_details are missing, try to resolve the Payment Request
    if not doctype or not docname:
        doctype, docname = resolve_payment_request_from_emi_webhook(data)
        if not docname:
            frappe.log_error(
                "EMI Webhook: Could not resolve Payment Request",
                frappe.as_json(data),
            )
            response["message"] = _(
                "EMI webhook received but could not resolve Payment Request"
            )
            return

    # Store UTR as reference_no
    disbursement_details = data.get("disbursement_details") or {}
    utr = disbursement_details.get("utr")

    # Update the document with transaction ID, reference_no and EMI payment status
    update_fields = {"transaction_id": application_code, "is_emi_payment": 1}
    if utr:
        update_fields["reference_no"] = utr
    db.set_value(doctype, docname, update_fields)

    # Retrieve the document using doctype and docname
    doc = get_doc(doctype, docname)

    # Update EMI status in the payment request
    event = data.get("event")
    timestamp = data.get("timestamp")
    update_emi_status(doc, event, timestamp)

    # If the event is 'emi.disbursed', mark the payment as authorized/completed
    if event == "emi.disbursed":
        # Check if already paid - avoid duplicate payment entry on duplicate webhook
        if doc.status == "Paid":
            response["message"] = _("EMI already processed")
            return

        # Set mode of payment for GrayQuest EMI payments
        ensure_mode_of_payment_exists("GrayQuest EMI")
        doc.db_set("mode_of_payment", "GrayQuest EMI")
        doc.reload()  # Refresh in-memory object for payment_entry()
        doc.on_payment_authorized(status="Completed")
        response["message"] = _("EMI Disbursed")
        return

    # Return success message for other events
    response["message"] = _("EMI Status Updated")


def resolve_payment_request_from_emi_webhook(data):
    """
    Resolve Payment Request when udf_details are missing (external EMI webhook).

    Matching strategy:
        1. Use student_uuid to find the Student in the system
        2. Find the latest submitted Fees for that student + academic_year
        3. Find an Initiated Payment Request linked to that Fees with matching amount
        4. Return (doctype, docname) if resolved, else (None, None)

    Args:
        data (dict): Full EMI webhook payload

    Returns:
        tuple: ("Payment Request", docname) if resolved, else (None, None)
    """
    student_details = data.get("student_details", {})
    fee_details = data.get("fee_details", {})
    disbursement_details = data.get("disbursement_details", {})

    student_uuid = student_details.get("student_uuid")
    amount = flt(disbursement_details.get("disbursed_amount") or fee_details.get("amount"))
    academic_year = student_details.get("academic_year")

    if not student_uuid or not academic_year or not amount:
        return None, None

    if not db.exists("Student", student_uuid):
        return None, None

    # Step 1: Find the latest submitted Fees for this student and academic year
    fees_filters = {
        "student": student_uuid,
        "academic_year": academic_year,
        "docstatus": 1,
    }

    fees_name = db.get_value("Fees", fees_filters, "name", order_by="creation desc")
    if not fees_name:
        return None, None

    # Step 2: Find Initiated Payment Request linked to this Fees with matching amount
    pr_filters = {
        "reference_doctype": "Fees",
        "reference_name": fees_name,
        "status": "Initiated",
        "docstatus": 1,
        "grand_total": amount,
    }

    pr_name = db.get_value("Payment Request", pr_filters, "name", order_by="creation asc")
    if pr_name:
        return "Payment Request", pr_name

    return None, None


def handle_response_web_form(data):
    udf_details = data.get("udf_details")
    doctype = udf_details.get("udf_1")
    docname = udf_details.get("udf_2")
    docname = docname.replace("@", "(").replace("#", ")")
    application_details = data.get("application_details")
    application_code = application_details.get("code")
    if data.get("event") == "dt.payment.captured":
        if db.exists(doctype, docname):
            db.set_value(doctype, docname, "transaction_id", application_code)
            doc = frappe.get_doc(doctype, docname, ignore_permissions=True)
            return doc.validate_payment(data)
        else:
            frappe.log_error(f"{doctype} {docname} does not exist")


def update_emi_status(doc, event, timestamp):
    """
    Update EMI Status in Payment Request

    Args:
        doc (Document): Payment Request document
        event (str): Webhook event
        timestamp (str): Webhook timestamp
    """
    if not timestamp:
        timestamp = now_datetime()
    elif isinstance(timestamp, str):
        timestamp = get_datetime(timestamp)
    status = EMI_STATUS_MAPPING.get(event)
    if status and status not in [d.status for d in doc.emi_status]:
        doc.append(
            "emi_status",
            {
                "status": status,
                "timestamp": timestamp,
            },
        )
        doc.save(ignore_permissions=True)
        doc.reload()


def add_webhook_log(data):
    """
    Add Webhook Log

    Args:
        data (dict): Webhook data
    """
    try:
        timestamp = data.get("timestamp")
        if not timestamp:
            timestamp = now_datetime()
        elif isinstance(timestamp, str):
            timestamp = get_datetime(timestamp)
        application_details = data.get("application_details", {})
        application_code = application_details.get("code")

        # Resolve reference document: Payment Request > Fees > empty
        doctype, docname = None, None
        try:
            udf_details = data.get("udf_details", {})
            resolved_dt, resolved_dn, _ = resolve_payment_request(udf_details)
            if resolved_dt and resolved_dn:
                doctype, docname = resolved_dt, resolved_dn
            elif data.get("entity") == "monthly-emi":
                resolved_dt, resolved_dn = resolve_payment_request_from_emi_webhook(data)
                if resolved_dt and resolved_dn:
                    doctype, docname = resolved_dt, resolved_dn
                else:
                    # Try to at least find the Fees document
                    student_uuid = (data.get("student_details") or {}).get("student_uuid")
                    academic_year = (data.get("student_details") or {}).get("academic_year")
                    if student_uuid and academic_year:
                        fees_name = db.get_value("Fees", {
                            "student": student_uuid,
                            "academic_year": academic_year,
                            "docstatus": 1,
                        }, "name", order_by="creation desc")
                        if fees_name:
                            doctype, docname = "Fees", fees_name
        except Exception:
            pass

        student = None
        try:
            if doctype and docname:
                if doctype == "Payment Request":
                    student = db.get_value(doctype, docname, "party")
                elif frappe.db.has_column(doctype, "student"):
                    student = db.get_value(doctype, docname, "student")
            if not student:
                student_uuid = (data.get("student_details") or {}).get("student_uuid")
                if student_uuid and db.exists("Student", student_uuid):
                    student = student_uuid
        except Exception:
            pass

        entity = data.get("entity")
        if entity == "direct":
            entity_type = "Payment Gateway"
        elif entity == "monthly-emi":
            entity_type = "Monthly EMI"
        else:
            entity_type = ""
        # Create a new Webhook Log document
        webhook_log = frappe.get_doc(
            {
                "doctype": "GrayQuest Webhook Log",
                "entity_type": entity_type,
                "event": data.get("event"),
                "timestamp": timestamp,
                "reference_id": data.get("reference_id"),
                "application_code": application_code,
                "reference_doctype": doctype,
                "reference_name": docname,
                "student": student,
                "data": frappe.json.dumps(data, indent=4),
            }
        )
        # Save the Webhook Log document
        webhook_log.insert(ignore_permissions=True, ignore_links=True)
        # Commit immediately to ensure log is saved even if payment processing fails later
        frappe.db.commit()
    except Exception:
        # Log the error
        frappe.log_error("GrayQuest Webhook Log Error", frappe.get_traceback())
        return False
    return True

def create_payment_entry(doc, amount=0, posting_date=None, reference_date=None, transaction_id=None):
    """
    Create Payment Entry for the given document
    Args:
        doc (Document): Document
        amount (int, optional): Amount. Defaults to 0.
        posting_date (str, optional): Posting Date. Defaults to None.
        reference_date (str, optional): Reference Date. Defaults to None.
    """
    try:
        user = frappe.session.user
        frappe.set_user("Administrator")
        if hasattr(doc, "school") and doc.school:
            paid_from = frappe.get_value("School", doc.school, "event_account")
            company = frappe.get_value("Account", paid_from, "company")
        else:
            company = frappe.db.get_single_value('Global Defaults', 'default_company')
            paid_from = frappe.get_value("Company", company, "default_receivable_account")
        paid_to = frappe.get_value("Company", company, "default_income_account")
        cost_center = frappe.get_value("Company", company, "cost_center")
        payment_entry = frappe.get_doc(
            {
                "doctype": "Payment Entry",
                "payment_type": "Receive",
                "company": company,
                "cost_center": cost_center,
                "posting_date": posting_date or frappe.utils.nowdate(),
                "reference_date": reference_date or frappe.utils.nowdate(),
                "party_type": "Student",
                "party": doc.student,
                "party_name": doc.get("student_name"),
                "paid_from": paid_from,
                "paid_to": paid_to,
                "paid_amount": amount,
                "received_amount": amount,
                "reference_doctype": doc.doctype,
                "reference_name": doc.name,
                "mode_of_payment": "Online",
                "reference_no": transaction_id,
                "school": doc.get("school"),
                "program": doc.get("class"),
            }
        )
        payment_entry.insert(ignore_permissions=True)
        payment_entry.submit()
        frappe.set_user(user)
        return payment_entry
    except Exception:
        frappe.log_error("Error While Creating Payment Entry for Web Form", frappe.get_traceback())
        return None
