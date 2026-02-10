# Copyright (c) 2024, Hybrowlabs Technologies and contributors
# For license information, please see license.txt
import base64

import frappe
import requests
from frappe import _, db, response
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.utils import call_hook_method
from payments.utils import create_payment_gateway

from grayquest.utils import get_payload
from grayquest.utils.webhook import (
    add_webhook_log,
    handle_emi_webhook,
    handle_payment_gateway_webhook,
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
        url = None
        try:
            url = self.generate_url(kwargs)
        except Exception:
            frappe.log_error(_("GrayQuest Payment Gateway Error"), frappe.get_traceback())
            frappe.logger("grayquest").exception(frappe.get_traceback())
        return url

    def generate_url(self, kwargs):
        payload = get_payload(self, kwargs)
        api_url = self.api_url.strip("/")
        slug = self.slug
        if kwargs.get("event", False):
            slug = self.event_slug
        endpoint = f"{api_url}/v1/pp/redirect/{slug}"

        reference_docname = kwargs.get("reference_docname")
        response = self.make_request(
            method="POST",
            endpoint=endpoint,
            payload=payload,
            reference_doctype=kwargs.get("reference_doctype"),
            reference_docname=reference_docname,
            request_description="GrayQuest Payment Request url",
        )

        if response.status_code == 201:
            return response.json().get("data", {}).get("redirection_url")
        else:
            response_data = response.json()
            frappe.log_error(_("GrayQuest Payment Gateway Error"), response_data)
            frappe.throw(
                _("GrayQuest Payment Error: {0}").format(response_data.get("message", "Unknown error")),
                title=_("Payment Gateway Error"),
            )

    def get_headers(self):
        if not (self.api_key and self.client_id and self.client_secret):
            frappe.throw(_("GrayQuest API Key, Client ID, and Client Secret are required"))

        client_secret = self.get_password("client_secret")
        api_key = self.get_password("api_key")

        # Encode client_id and client_secret in base64
        credentials = f"{self.client_id}:{client_secret}"
        auth_token = base64.b64encode(credentials.encode()).decode()

        return {
            "Authorization": f"Basic {auth_token}",
            "GQ-API-Key": api_key,
            "Content-Type": "application/json",
        }

    def log_request(self,service_name, data, url=None, **kwargs):
        """Create an Integration Request log entry for GrayQuest API calls."""
        return create_request_log(
            data=data,
            service_name=service_name,
            url=url,
            **kwargs,
        )

    def make_request(self, method, endpoint, payload=None, params=None,
                     reference_doctype=None, reference_docname=None,
                     request_description=None):
        """
        Make an HTTP request to GrayQuest API and log it via Integration Request.

        Args:
            method: HTTP method ("GET" or "POST")
            endpoint: Full API endpoint URL
            payload: JSON body for POST requests
            params: Query parameters for GET requests
            reference_doctype: Linked document type for logging
            reference_docname: Linked document name for logging
            request_description: Description for the Integration Request log

        Returns:
            requests.Response: Response from the API
        """
        headers = self.get_headers()

        redacted_headers = {
            key: "******" if key in ("Authorization", "GQ-API-Key") else value
            for key, value in headers.items()
        }

        integration_request = self.log_request(
            service_name="GrayQuest",
            data=payload or params or {},
            url=endpoint,
            request_headers=redacted_headers,
            reference_doctype=reference_doctype,
            reference_docname=reference_docname,
            request_description=request_description,
        )

        try:
            if method == "POST":
                response = requests.post(endpoint, headers=headers, json=payload)
            elif method == "GET":
                response = requests.get(endpoint, headers=headers, params=params)
            else:
                frappe.throw(_("Unsupported HTTP method: {0}").format(method))

            response_data = response.json()

            if response.ok:
                integration_request.handle_success(response_data)
            else:
                integration_request.handle_failure(response_data)
            frappe.db.commit()
            return response
        except Exception:
            integration_request.handle_failure({"error": frappe.get_traceback()})
            raise

    def handle_webhook(self, data):
        self.add_webhook_log(data)

        try:
            if data.get("entity") == "direct":
                result = handle_payment_gateway_webhook(data)
            elif data.get("entity") == "monthly-emi":
                result = handle_emi_webhook(data)
            else:
                frappe.log_error(_("Invalid Webhook Entity"))
                return {"status": "error", "message": _("Invalid Webhook Entity")}
            return result
        except Exception:
            raise

    def add_webhook_log(self, data):
        add_webhook_log(data)

    def check_payment_status(self, payment_request):
        """
        Check the payment status of the transaction using the GrayQuest API.
        if paid then mark the payment request as paid.
        """
        api_url = self.api_url.strip("/")
        endpoint = f"{api_url}/v1/payments/fetch"
        transaction_id = db.get_value("Payment Request", payment_request, "transaction_id")
        params = {"application_code": transaction_id}

        res = self.make_request(
            method="GET",
            endpoint=endpoint,
            params=params,
            reference_doctype="Payment Request",
            reference_docname=payment_request,
            request_description="GrayQuest Payment Status Check",
        )

        if res.status_code != 200:
            frappe.log_error(_("GrayQuest Payment Gateway Error"), res.json())

        response["message"] = res.json()
