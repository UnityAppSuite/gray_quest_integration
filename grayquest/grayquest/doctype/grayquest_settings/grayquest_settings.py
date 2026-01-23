# Copyright (c) 2024, Hybrowlabs Technologies and contributors
# For license information, please see license.txt
import base64

import frappe
import requests
from frappe import _, db, response
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

    def generate_url(self,kwargs):
        headers = self.get_headers()
        payload = get_payload(self, kwargs)
        api_url = self.api_url.strip("/")
        slug = self.slug
        if kwargs.get("event", False):
            slug = self.event_slug
        endpoint = f"{api_url}/v1/pp/redirect/{slug}"

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

    def _request_payment_url(self, payload, context="Payment"):
        """
        Make API request to GrayQuest and return payment URL.

        Args:
            payload: Request payload dict
            context: Context string for error logging (e.g., "Fees", "Applicant")

        Returns:
            str: Payment URL from GrayQuest API

        Raises:
            frappe.ValidationError: When API call fails
        """
        headers = self.get_headers()
        if not headers:
            frappe.throw("GrayQuest API credentials are not configured properly")

        api_url = self.api_url.strip("/")
        endpoint = f"{api_url}/v1/pp/redirect/{self.slug}"

        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=30)

            try:
                response_data = response.json()
            except ValueError:
                frappe.log_error(
                    title=f"GrayQuest {context} Invalid Response",
                    message=f"Status: {response.status_code}\nResponse: {response.text[:500]}"
                )
                frappe.throw(f"GrayQuest returned invalid response (Status: {response.status_code})")

            if response.status_code == 201:
                url = response_data.get("data", {}).get("redirection_url")
                if url:
                    return url
                frappe.throw("GrayQuest API did not return a payment URL")

            error_msg = response_data.get("message") or response_data.get("error") or str(response_data)
            frappe.log_error(
                title=f"GrayQuest {context} URL Error",
                message=f"Status: {response.status_code}\nResponse: {response_data}"
            )
            frappe.throw(f"GrayQuest API Error: {error_msg}")

        except requests.exceptions.Timeout:
            frappe.log_error("GrayQuest API Timeout", f"Endpoint: {endpoint}")
            frappe.throw("GrayQuest API request timed out. Please try again.")
        except requests.exceptions.RequestException as e:
            frappe.log_error("GrayQuest API Connection Error", frappe.get_traceback())
            frappe.throw(f"Failed to connect to GrayQuest: {str(e)}")

    def generate_payment_url(self, **kwargs):
        """Generate payment URL for direct Fees payment."""
        from grayquest.utils import get_fees_payload
        payload = get_fees_payload(self, kwargs)
        return self._request_payment_url(payload, context="Fees")

    def get_payment_url_applicant(self, **kwargs):
        """Generate payment URL for Student Applicant deposit payment."""
        from grayquest.utils import get_applicant_payload_direct
        payload = get_applicant_payload_direct(kwargs)
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
        from frappe.auth import LoginManager

        try:
            login_manager = LoginManager()
            login_manager.login_as("Administrator")

            # Extract data
            udf_details = data.get("udf_details", {})
            doctype = udf_details.get("udf_1")
            docname = udf_details.get("udf_2")
            payment_term = udf_details.get("udf_3")

            application_details = data.get("application_details", {})
            transaction_id = application_details.get("code")

            payment_details = data.get("payment_details", {})
            status = payment_details.get("status")
            amount = payment_details.get("amount")

            if status != "PAID":
                return {"message": "Payment not completed"}

            if not frappe.db.exists(doctype, docname):
                frappe.log_error(f"GrayQuest: {doctype} {docname} does not exist")
                return {"message": "Document not found"}

            doc = frappe.get_doc(doctype, docname, ignore_permissions=True)

            if doctype == "Fees":
                doc.on_payment_authorized(
                    status="Completed",
                    payment_term=payment_term,
                    transaction_id=transaction_id,
                    amount=amount
                )
                return {"message": "Payment Successful"}

            elif doctype == "Student Applicant":
                result = doc.on_payment_authorized(
                    status="Completed",
                    transaction_id=transaction_id,
                    amount=amount
                )
                return result or {"message": "Applicant Payment Successful"}

            else:
                frappe.log_error(f"GrayQuest: Unsupported doctype {doctype}")
                return {"message": "Unsupported document type"}

        except Exception as e:
            frappe.log_error("GrayQuest handle_response Error", frappe.get_traceback())
            return {"message": f"Payment processing failed: {str(e)}"}
        finally:
            try:
                login_manager.logout()
            except Exception:
                pass
