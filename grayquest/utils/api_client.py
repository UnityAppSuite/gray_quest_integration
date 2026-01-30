# Copyright (c) 2025, Hybrowlabs Technologies and contributors
# For license information, please see license.txt

import json
import time

import frappe
import requests

from grayquest.grayquest.doctype.grayquest_api_log.grayquest_api_log import create_api_log


def make_request(
    url: str,
    data: dict | None = None,
    headers: dict | None = None,
    service: str = "API Request",
    method: str = "POST",
    timeout: int = 30,
    is_json: bool = True,
) -> dict:
    """Make HTTP request to GrayQuest API with logging."""
    request_id = frappe.generate_hash(length=10)
    start_time = time.time()

    response = None
    status_code = None
    response_text = None
    error = None
    is_success = False
    result = {}

    try:
        if method.upper() == "POST":
            if is_json:
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
            else:
                response = requests.post(url, headers=headers, data=data, timeout=timeout)
        elif method.upper() == "GET":
            response = requests.get(url, headers=headers, params=data, timeout=timeout)
        else:
            response = requests.request(method, url, headers=headers, json=data, timeout=timeout)

        status_code = response.status_code
        response_text = response.text or None

        try:
            result = response.json()
        except (ValueError, json.JSONDecodeError):
            result = {"raw_response": response.text}

        # GrayQuest uses status code 201 for successful payment URL creation
        if status_code in (200, 201):
            is_success = True
        else:
            error = result.get("message") or result.get("error") or f"Request failed: {status_code}"

        return result

    except Exception as e:
        error = f"{e!s}\n\n{frappe.get_traceback()}"
        frappe.log_error(title="GrayQuest API Error", message=f"URL: {url}\nService: {service}\n\n{error}")
        return {"status": "error", "message": "Unable to connect to payment gateway."}

    finally:
        execution_time_ms = int((time.time() - start_time) * 1000)
        try:
            create_api_log(
                request_id=request_id,
                service=service,
                http_method=method,
                full_url=url,
                request_headers=headers,
                request_payload=data,
                status_code=status_code,
                response_body=response_text,
                execution_time_ms=execution_time_ms,
                is_success=is_success,
                error_details=error,
            )
        except Exception:
            pass
