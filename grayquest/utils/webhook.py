import frappe
from frappe import _, db, get_doc, response
from frappe.utils import flt, get_datetime, now_datetime

from grayquest.utils import (
    EMI_IN_FLIGHT_EVENTS,
    EMI_STATUS_MAPPING,
    EMI_UNSUCCESSFUL_EVENTS,
)


def handle_payment_gateway_webhook(data):
    """
    Handle Payment Gateway Webhook - supports both Payment Request and direct Fees

    Args:
        data (dict): Webhook data

    Details:
        - If event is `dt.payment.order.created`, do nothing
        - If event is `dt.payment.captured`, update application code and call on_payment_authorized
        - the udf_details contains `Payment Request` doctype and docname (legacy)
        - OR udf_details contains `Fees` or `Student Applicant` doctype for direct payments
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
            payment_term = udf_details.get("udf_3")
            # Extract application details from the data
            application_details = data.get("application_details")
            # Get application code from application details
            application_code = application_details.get("code")
            # Get payment details from the data
            payment_details = data.get("payment_details", {})
            amount = payment_details.get("amount")

            # Check if already paid - avoid re-processing
            if doctype == "Payment Request":
                current_status = db.get_value(doctype, docname, "status")
                if current_status == "Paid":
                    # Already processed via callback - just acknowledge webhook
                    response["message"] = _("Payment already processed via callback")
                    return

            # Fetch the document using doctype and docname
            doc = get_doc(doctype, docname)

            # Handle direct Fees payment (new flow like Easebuzz)
            if doctype == "Fees" and payment_details.get("status") == "PAID":
                doc.on_payment_authorized(
                    status="Completed",
                    payment_term=payment_term,
                    transaction_id=application_code,
                    amount=amount
                )
                response["message"] = _("Fee payment processed successfully")
                return

            # Handle direct Student Applicant payment
            if doctype == "Student Applicant" and payment_details.get("status") == "PAID":
                result = doc.on_payment_authorized(
                    status="Completed",
                    transaction_id=application_code,
                    amount=amount
                )
                response["message"] = result.get("message") if result else _("Applicant payment processed")
                return

            # Existing Payment Request flow (keep for backward compatibility)
            # Update the transaction_id field in the document
            if hasattr(doc, "transaction_id"):
                doc.db_set("transaction_id", application_code)
            if hasattr(doc, "paid_amount"):
                doc.db_set("paid_amount", amount)
            # Call the on_payment_authorized method on the document
            if payment_details.get("status") == "PAID":
                # Route based on payment_term for one-time payments
                if payment_term == "one_time" and hasattr(doc, "reference_doctype") and doc.reference_doctype == "Student Applicant":
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
    Handle EMI Webhook, and update EMI Status in Payment Request or Fees

    Args:
        data (dict): Webhook data

    Details:
        - udf_details contains doctype (Payment Request or Fees) and docname
        - udf_3 carries the installment the application was raised for
        - EMI fields (is_emi_payment, emi_application_code) stored on Fees parent doc
        - the blocking flag lives on the matching Payment Schedule row, so funding
          one installment never blocks payment of the next one
    """
    try:
        # Extract user-defined fields (udf) details from the webhook data
        udf_details = data.get("udf_details", {})
        doctype = udf_details.get("udf_1")
        docname = udf_details.get("udf_2")
        payment_term = udf_details.get("udf_3")

        # Extract application details from the webhook data
        application_details = data.get("application_details")
        application_code = application_details.get("code")

        # Extract event and timestamp
        event = data.get("event")
        timestamp = data.get("timestamp")

        # Handle Fees doctype
        if doctype == "Fees":
            doc = get_doc(doctype, docname)

            # Set EMI fields on Fees parent doc. The parent flag records that this
            # fee is EMI funded (it drives the EMI Details tab and discount gating);
            # it is deliberately not what the payment portal blocks on.
            doc.emi_application_code = application_code
            if event == "emi.process.completed" or event == "emi.disbursed":
                doc.is_emi_payment = 1
            elif event in EMI_UNSUCCESSFUL_EVENTS:
                doc.is_emi_payment = 0

            # Mark/clear the blocking flag on the installment this application belongs to
            set_term_emi_flag(doc, payment_term, event)

            # Update EMI status in the fees document
            update_emi_status(doc, event, timestamp, payment_term)

            # Remove payment plan discount once user has committed to EMI
            if event in ("emi.form.submitted", "emi.process.completed", "emi.disbursed"):
                doc.remove_payment_plan_discount()

            # If the event is 'emi.disbursed', handle based on tranche type
            if event == "emi.disbursed":
                notes = data.get("notes", {}) or {}
                is_second_disbursal = bool(notes.get("id"))
                message = doc.handle_emi_payment(
                    application_code, is_second_disbursal, payment_term=payment_term
                )
                response["message"] = message
            else:
                response["message"] = _("EMI Status Updated")

        # Handle Payment Request doctype (legacy)
        else:
            # Update the document with transaction ID and EMI payment status
            db.set_value(
                doctype, docname, {"transaction_id": application_code, "is_emi_payment": 1}
            )

            # Retrieve the document using doctype and docname
            doc = get_doc(doctype, docname)

            # Update EMI status in the payment request
            update_emi_status(doc, event, timestamp, payment_term)

            # If the event is 'emi.disbursed', mark the payment as authorized/completed
            if event == "emi.disbursed":
                doc.on_payment_authorized(status="Completed")
                response["message"] = _("EMI Disbursed")
            else:
                response["message"] = _("EMI Status Updated")

    except Exception as e:
        # Log the error and return an error message
        frappe.log_error("EMI Webhook Error", frappe.get_traceback())
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


def set_term_emi_flag(doc, payment_term, event):
    """
    Mark or clear the EMI blocking flag on a single installment.

    Args:
        doc (Document): Fees document
        payment_term (str): Installment the EMI application was raised for (udf_3)
        event (str): Webhook event

    Details:
        - the flag is set while the application is in flight, and cleared on every
          terminal event (disbursed, process completed, rejected, backout, closed)
        - an installment EMI has already settled keeps the flag: there it is no longer
          a portal block (a paid installment is never offered) but the record that this
          installment was EMI funded, and the events that trail a disbursal must not
          erase it
        - written with db.set_value so it persists regardless of the parent's save path
        - a payload without udf_3 is left alone rather than guessed at
    """
    if not payment_term:
        return

    in_flight = 1 if event in EMI_IN_FLIGHT_EVENTS else 0
    for schedule in doc.payment_schedule:
        if str(schedule.payment_term) == str(payment_term):
            if not in_flight and flt(schedule.outstanding) <= 0:
                return

            db.set_value(
                "Payment Schedule", schedule.name, "is_emi_payment", in_flight, update_modified=False
            )
            schedule.is_emi_payment = in_flight
            # set_value only busts the child row's cache, but the payment portal reads
            # the parent through get_cached_doc - without this it serves stale rows.
            frappe.clear_document_cache("Fees", doc.name)
            return


def update_emi_status(doc, event, timestamp, payment_term=None):
    """
    Update EMI Status in Payment Request or Fees

    Args:
        doc (Document): Payment Request or Fees document
        event (str): Webhook event
        timestamp (str): Webhook timestamp
        payment_term (str): Installment the EMI application was raised for (udf_3)
    """
    if not timestamp:
        timestamp = now_datetime()
    elif isinstance(timestamp, str):
        timestamp = get_datetime(timestamp)
        # Strip timezone info for MySQL compatibility
        if hasattr(timestamp, 'replace') and timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)

    status = EMI_STATUS_MAPPING.get(event)
    if not status:
        return

    row = {
        "status": status,
        "timestamp": timestamp,
    }
    if payment_term:
        row["payment_term"] = payment_term

    doc.append("emi_status", row)
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
            # Strip timezone info for MySQL compatibility
            if hasattr(timestamp, 'replace') and timestamp.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=None)
        application_details = data.get("application_details", {})
        application_code = application_details.get("code")
        udf_details = data.get("udf_details", {})
        doctype = udf_details.get("udf_1")
        docname = udf_details.get("udf_2")
        if doctype == "Payment Request":
            student = db.get_value(doctype, docname, "party")
        elif doctype == "Fees":
            student = db.get_value(doctype, docname, "student")
        elif doctype and frappe.db.has_column(doctype, "student"):
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
