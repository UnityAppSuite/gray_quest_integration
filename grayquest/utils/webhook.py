import frappe
from frappe import _, db, get_doc, response
from frappe.utils import get_datetime, now_datetime

from grayquest.utils import EMI_STATUS_MAPPING


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
    try:
        if data.get("event") == "dt.payment.captured":
            # Extract udf_details from the data
            udf_details = data.get("udf_details", {})
            # Get doctype and docname from udf_details
            doctype = udf_details.get("udf_1")
            docname = udf_details.get("udf_2")
            fee_type = udf_details.get("udf_3")
            # Extract application details from the data
            application_details = data.get("application_details")
            # Get application code from application details
            application_code = application_details.get("code")

            # Check if already paid via callback - avoid re-processing
            current_status = db.get_value(doctype, docname, "status")
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
            # Update the transaction_id field in the document
            if hasattr(doc, "transaction_id"):
                doc.db_set("transaction_id", application_code)
            if hasattr(doc, "paid_amount"):
                doc.db_set("paid_amount", amount)
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
            # Get doctype and docname from udf_details
            doctype = udf_details.get("udf_1")
            docname = udf_details.get("udf_2")
            # Fetch the document using doctype and docname
            doc = get_doc(doctype, docname)
            if hasattr(doc, "validate_payment_order_created"):
                res = doc.validate_payment_order_created(data)
                if res:
                    response["message"] = res
                else:
                    response["message"] = _("Payment order created, awaiting completion.")

        elif data.get("event") == "dt.payment.failed":
            udf_details = data.get("udf_details", {})
            # Get doctype and docname from udf_details
            doctype = udf_details.get("udf_1")
            docname = udf_details.get("udf_2")
            doc = get_doc(doctype, docname)
            if hasattr(doc, "validate_failed_payment"):
                res = doc.validate_failed_payment(data)
                if res:
                    response["message"] = res
                else:
                    response["message"] = _("Payment failed. Please try again or contact support.")

            # Return success response
    except Exception as e:
        # Log the error and return error response
        frappe.log_error(
            f"Payment Gateway Webhook Error: {str(e)}", frappe.get_traceback()
        )
        response["message"] = _("Error in Payment Gateway Webhook")


def handle_emi_webhook(data):
    """
    Handle EMI Webhook, and update EMI Status in payment request

    Args:
        data (dict): Webhook data

    Details:
        - the udf_details contains `Payment Request` doctype and docname
    """
    try:
        # Extract user-defined fields (udf) details from the webhook data
        udf_details = data.get("udf_details", {})
        doctype = udf_details.get("udf_1")
        docname = udf_details.get("udf_2")

        # Extract application details from the webhook data
        application_details = data.get("application_details")
        application_code = application_details.get("code")

        # Update the document with transaction ID and EMI payment status
        db.set_value(
            doctype, docname, {"transaction_id": application_code, "is_emi_payment": 1}
        )

        # Retrieve the document using doctype and docname
        doc = get_doc(doctype, docname)

        # Update EMI status in the payment request
        event = data.get("event")
        timestamp = data.get("timestamp")
        update_emi_status(doc, event, timestamp)

        # If the event is 'emi.disbursed', mark the payment as authorized/completed
        if event == "emi.disbursed":
            doc.on_payment_authorized(status="Completed")
            response["message"] = _("EMI Disbursed")

        # Return success message for other events
        response["message"] = _("EMI Status Updated")
    except Exception as e:
        # Log the error and return an error message
        frappe.log_error(f"EMI Webhook Error: {str(e)}", frappe.get_traceback())
        response["message"] = _("Error in EMI Webhook")


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
        udf_details = data.get("udf_details", {})
        doctype = udf_details.get("udf_1")
        docname = udf_details.get("udf_2")
        if doctype == "Payment Request":
            student = db.get_value(doctype, docname, "party")
        elif frappe.db.has_column(doctype, "student"):
            student = db.get_value(doctype, docname, "student")
        else:
            student = None
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
        webhook_log.insert(ignore_permissions=True)
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
