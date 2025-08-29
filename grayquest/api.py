import frappe


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook_handler(**kwargs):
    data = frappe.parse_json(kwargs)
    controller = frappe.get_last_doc("GrayQuest Settings")
    controller.handle_webhook(data)


@frappe.whitelist()
def check_payment_status(payment_request):
    """
    Check the payment status of the transaction using the GrayQuest API.
    if paid then mark the payment request as paid.
    """
    controller = frappe.get_last_doc("GrayQuest Settings")
    controller.check_payment_status(payment_request)
