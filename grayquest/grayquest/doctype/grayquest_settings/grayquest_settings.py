# Copyright (c) 2024, Hybrowlabs Technologies and contributors
# For license information, please see license.txt
import frappe
import base64
import requests
from frappe import _, db, response
from frappe.model.document import Document
from frappe.utils import call_hook_method
from payments.utils import create_payment_gateway
from grayquest.utils import get_payload
from grayquest.utils.webhook import (
    handle_payment_gateway_webhook,
    handle_emi_webhook,
    add_webhook_log,
)


class GrayQuestSettings(Document):
    supported_currencies = ("INR",)

    def after_insert(self):
        create_payment_gateway("GrayQuest", self.doctype, self.name)
        call_hook_method("payment_gateway_enabled", gateway="GrayQuest")

    def validate_transaction_currency(self, currency):
        if currency not in self.supported_currencies:
            frappe.throw(
                _(
                    "Please select another payment method. Razorpay does not support transactions in currency '{0}'"
                ).format(currency)
            )

    def get_payment_url(self, **kwargs):
        try:
            url = self.generate_url(kwargs)
        except Exception as e:
            frappe.log_error(_(f"GrayQuest Payment Gateway Error: {str(e)}"), frappe.get_traceback())
            frappe.logger("grayquest").exception(frappe.get_traceback())
        return url

    def generate_url(self, kwargs):
        headers = self.get_headers()
        payload = get_payload(self, kwargs)
        api_url = self.api_url.strip("/")
        endpoint = f"{api_url}/v1/pp/redirect/{self.slug}"

        response = requests.post(endpoint, headers=headers, json=payload)
        if response.status_code == 201:
            return response.json().get("data", {}).get("redirection_url")
        else:
            frappe.log_error(_("GrayQuest Payment Gateway Error"), response.json())
            return response.json().get("message")

    def get_headers(self):
        if self.api_key and self.client_id and self.client_secret:
            client_secret = self.get_password("client_secret")
            api_key = self.get_password("api_key")

            # Encode client_id and client_secret in base64
            credentials = f"{self.client_id}:{client_secret}"
            auth_token = base64.b64encode(credentials.encode()).decode()

            # Headers
            return {
                "Authorization": f"Basic {auth_token}",
                "GQ-API-Key": api_key,
                "Content-Type": "application/json",
            }

    def handle_webhook(self, data):
        # Add webhook log
        self.add_webhook_log(data)
        if data.get("entity") == "direct":
            return handle_payment_gateway_webhook(data)
        elif data.get("entity") == "monthly-emi":
            return handle_emi_webhook(data)
        else:
            frappe.log_error(_("Invalid Webhook Entity"))
            return {
                "status": "error",
                "message": _("Invalid Webhook Entity"),
            }

    def add_webhook_log(self, data):
        add_webhook_log(data)

    def check_payment_status(self, payment_request):
        """
        Check the payment status of the transaction using the GrayQuest API.
        if paid then mark the payment request as paid.
        """
        api_url = self.api_url.strip("/")
        endpoint = f"{api_url}/v1/payments/fetch"
        headers = self.get_headers()
        transaction_id = db.get_value("Payment Request", payment_request, "transaction_id")
        payload = {"application_code": transaction_id}
        res = requests.get(endpoint, headers=headers, params=payload)

        if res.status_code == 200:
            response["message"] = res.json()
        else:
            frappe.log_error(_("GrayQuest Payment Gateway Error"), res.json())
            response["message"] = res.json()
