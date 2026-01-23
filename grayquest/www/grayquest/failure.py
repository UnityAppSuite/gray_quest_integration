import frappe

no_cache = 1


def get_context(context):
    """
    Handle redirect from GrayQuest after payment failure.
    Redirects directly to payment page.
    """
    try:
        data = dict(frappe.form_dict) if frappe.form_dict else {}

        # Extract identifiers for redirect
        fee_hash = data.get("udf_5") or data.get("udf5") or data.get("fee_hash")
        applicant_id = None

        udf_1 = data.get("udf_1") or data.get("udf1")
        if udf_1 == "Student Applicant":
            applicant_id = data.get("udf_2") or data.get("udf2")

        if applicant_id:
            redirect_url = f"/payment?applicant_id={applicant_id}"
        elif fee_hash:
            redirect_url = f"/payment?fee_id={fee_hash}"
        else:
            redirect_url = "/"

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = redirect_url

    except Exception:
        frappe.log_error("GrayQuest Failure Page Error", frappe.get_traceback())
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = "/"
