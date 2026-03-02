import frappe

no_cache = 1


def get_context(context):
    """
    Handle redirect from GrayQuest after payment completion.

    GrayQuest sends payment data via query params or POST body.
    We extract the data, call handle_response, and redirect to payment page.
    """
    try:
        # Get data from request (GrayQuest may send via GET params or POST body)
        data = {}

        # Try to get from form_dict (handles both GET and POST)
        if frappe.form_dict:
            data = dict(frappe.form_dict)

        # If data has nested structure from GrayQuest webhook format
        if not data.get("udf_details") and data:
            # GrayQuest may send flat params - reconstruct udf_details
            udf_details = {
                "udf_1": data.get("udf_1") or data.get("udf1"),
                "udf_2": data.get("udf_2") or data.get("udf2"),
                "udf_3": data.get("udf_3") or data.get("udf3"),
                "udf_4": data.get("udf_4") or data.get("udf4"),
                "udf_5": data.get("udf_5") or data.get("udf5"),
            }
            application_details = {
                "code": data.get("application_code") or data.get("transaction_id") or data.get("txnid"),
            }
            payment_details = {
                "status": data.get("status", "").upper() or "PAID",
                "amount": data.get("amount"),
            }

            data = {
                "udf_details": udf_details,
                "application_details": application_details,
                "payment_details": payment_details,
            }

        # Extract udf_details for routing
        udf_details = data.get("udf_details", {})
        doctype = udf_details.get("udf_1")
        payment_term = udf_details.get("udf_3")
        fee_hash = udf_details.get("udf_5")
        applicant_id = udf_details.get("udf_2") if doctype == "Student Applicant" else None

        # Build redirect URL to the original payment/fee page
        if doctype == "Student Applicant" and applicant_id:
            redirect_url = f"/payment?applicant_id={applicant_id}"
        elif fee_hash:
            redirect_url = f"/payment?fee_id={fee_hash}"
        else:
            # Fallback - try to get fee_hash from Fees document
            docname = udf_details.get("udf_2")
            if doctype == "Fees" and docname:
                fee_hash = frappe.db.get_value("Fees", docname, "fee_hash")
                if fee_hash:
                    redirect_url = f"/payment?fee_id={fee_hash}"
                else:
                    redirect_url = "/"
            else:
                redirect_url = "/"

        # EMI: payment_term is "EMI" or not available in redirect data
        # Don't process anything, just show EMI message and redirect to fee page
        if doctype == "Fees" and (not payment_term or payment_term == "EMI"):
            context.title = "EMI Form Submitted"
            context.message = "You will get the receipt once the disbursal is successful."
            context.redirect_url = redirect_url
            context.is_emi = True
            return

        # Non-EMI: process response via handle_response
        controller = frappe.get_last_doc("GrayQuest Settings")
        controller.handle_response(data)

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = redirect_url

    except Exception:
        frappe.log_error("GrayQuest Success Page Error", frappe.get_traceback())
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "/"
