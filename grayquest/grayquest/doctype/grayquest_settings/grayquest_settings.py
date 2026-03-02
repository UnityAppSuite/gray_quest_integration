# Copyright (c) 2024, Hybrowlabs Technologies and contributors
# For license information, please see license.txt
import base64

import frappe
import requests
from frappe import _, db, response
from frappe.auth import LoginManager
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

        if response.ok:
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
            frappe.throw(
                _("GrayQuest API credentials are not configured. Please set API Key, Client ID, and Client Secret in GrayQuest Settings."),
                title=_("GrayQuest Configuration Error"),
            )

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

    def log_request(self, service_name, data, url=None, **kwargs):
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
        # Add webhook log
        self.add_webhook_log(data)

        # Login as Administrator for webhook processing (same pattern as Easebuzz)
        # Webhooks run as Guest user and need elevated permissions to create Payment Entries
        login_manager = LoginManager()
        try:
            login_manager.login_as("Administrator")

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
        except Exception as e:
            frappe.log_error(f"GrayQuest Webhook Error: {str(e)}", frappe.get_traceback())
            return {
                "status": "error",
                "message": _("Webhook processing failed"),
            }
        finally:
            login_manager.logout()

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

        if not res.ok:
            frappe.log_error(_("GrayQuest Payment Gateway Error"), res.json())

        response["message"] = res.json()

    def _request_payment_url(self, payload, context="Payment"):
        """Make API request to GrayQuest and return payment URL."""
        if not (self.api_key and self.client_id and self.client_secret):
            frappe.log_error(
                title="GrayQuest Configuration Error",
                message="API credentials not configured (api_key, client_id, or client_secret missing)"
            )
            frappe.throw("Payment gateway is not configured. Please contact support.")

        api_url = self.api_url.strip("/")
        endpoint = f"{api_url}/v1/pp/redirect/{self.slug}"

        res = self.make_request(
            method="POST",
            endpoint=endpoint,
            payload=payload,
            request_description=f"GrayQuest {context} Payment Request",
        )

        if res.ok:
            url = res.json().get("data", {}).get("redirection_url")
            if url:
                return url

        response_data = res.json()
        error_msg = response_data.get("message") or response_data.get("error") or f"Request failed: {res.status_code}"
        frappe.log_error(
            title=f"GrayQuest {context} API Error",
            message=f"Endpoint: {endpoint}\nResponse: {error_msg}"
        )
        frappe.throw("Unable to process payment. Please try again later.")

    def generate_payment_url(self, **kwargs):
        """Generate payment URL for direct Fees payment."""
        from grayquest.utils import get_fees_payload
        payload = get_fees_payload(self, kwargs)
        return self._request_payment_url(payload, context="Fees")

    def get_payment_url_applicant(self, **kwargs):
        """Generate payment URL for Student Applicant deposit payment."""
        from grayquest.utils import get_applicant_payload_direct
        payload = get_applicant_payload_direct(self, kwargs)
        return self._request_payment_url(payload, context="Applicant")

    def handle_response(self, data):
        """
        Handle payment response from GrayQuest success page (like Easebuzz handle_response).

        Extracts udf_details and calls appropriate on_payment_authorized method.

        Args:
            data: Response data from GrayQuest containing:
                - udf_details: {udf_1: doctype, udf_2: docname, udf_3: payment_term or "applicant"}
                - application_details: {code: transaction_id}
                - payment_details: {status: "PAID", amount: amount}

        Returns:
            dict: Result message
        """
        try:
            # Extract data
            udf_details = data.get("udf_details", {})
            doctype = udf_details.get("udf_1")
            docname = udf_details.get("udf_2")


            payment_details = data.get("payment_details", {})
            status = payment_details.get("status")

            if not frappe.db.exists(doctype, docname):
                frappe.log_error(f"GrayQuest: {doctype} {docname} does not exist")
                return {"message": "Document not found"}

            if doctype == "Fees":
                if status != "PAID":
                    return {"message": "Payment not completed"}
                return {"message": "Payment successful for Fees"}

            elif doctype == "Student Applicant":
                if status != "PAID":
                    return {"message": "Payment not completed"}
                return {"message": "Payment successful for Student Applicant"}
            else:
                frappe.log_error(f"GrayQuest: Unsupported doctype {doctype}")
                return {"message": "Unsupported document type"}

        except Exception as e:
            frappe.log_error("GrayQuest handle_response Error", frappe.get_traceback())
            return {"message": f"Payment processing failed: {str(e)}"}
