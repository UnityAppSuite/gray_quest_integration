import frappe
from frappe import _, get_doc, db, response
from frappe.utils import now_datetime, get_datetime
from grayquest.utils import EMI_STATUS_MAPPING


def handle_payment_gateway_webhook(data):
    """
    Handle Payment Gateway Webhook

    Args:
        data (dict): Webhook data

    Details:
        - If event is `dt.payment.order.created`, do nothing
        - If event is `dt.payment.captured`, update application code and call on_payment_authorized
        - the udf_details contains `Rayment Request` doctype and docname
    """
    try:
        if data.get("event") == "dt.payment.captured":
            # Extract udf_details from the data
            udf_details = data.get("udf_details", {})
            # Get doctype and docname from udf_details
            doctype = udf_details.get("udf_1")
            docname = udf_details.get("udf_2")
            # Extract application details from the data
            application_details = data.get("application_details")
            # Get application code from application details
            application_code = application_details.get("code")
            # Fetch the document using doctype and docname
            doc = get_doc(doctype, docname)
            # Update the transaction_id field in the document
            db.set_value(doctype, docname, "transaction_id", application_code)
            # Call the on_payment_authorized method on the document
            doc.on_payment_authorized(status="Completed")
            # Return success response
            response["message"] = _("Payment Captured")
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