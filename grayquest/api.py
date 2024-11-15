import frappe

@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook_handler(**kwargs):
    data = frappe.parse_json(kwargs)
    controller = frappe.get_last_doc("GrayQuest Settings")
    controller.handle_webhook(data)
