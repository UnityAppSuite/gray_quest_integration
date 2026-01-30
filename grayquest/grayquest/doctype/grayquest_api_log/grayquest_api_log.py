# Copyright (c) 2025, Hybrowlabs Technologies and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.model.document import Document


class GrayQuestAPILog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		curl_command: DF.Code | None
		error_category: DF.Literal["", "Timeout", "Connection Error", "Validation Error", "Authentication Error", "Authorization Error", "Not Found", "Rate Limit", "Server Error", "Unexpected Error"]
		error_details: DF.LongText | None
		execution_time_ms: DF.Int
		full_url: DF.SmallText | None
		http_method: DF.Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
		is_success: DF.Check
		request_headers: DF.Code | None
		request_id: DF.Data
		request_payload: DF.Code | None
		response_body: DF.Code | None
		service: DF.Data | None
		status_code: DF.Int
		timestamp: DF.Datetime
	# end: auto-generated types

	pass


def create_api_log(
	request_id: str,
	service: str,
	http_method: str,
	full_url: str,
	request_headers: dict | None = None,
	request_payload: dict | None = None,
	status_code: int | None = None,
	response_body: str | None = None,
	execution_time_ms: int | None = None,
	is_success: bool = False,
	error_category: str | None = None,
	error_details: str | None = None,
) -> str | None:
	"""Create a new GrayQuest API Log entry."""
	try:
		curl_command = _generate_curl_command(http_method, full_url, request_headers, request_payload)
		log_doc = frappe.get_doc({
			"doctype": "GrayQuest API Log",
			"request_id": request_id,
			"timestamp": frappe.utils.now_datetime(),
			"service": service,
			"http_method": http_method,
			"full_url": full_url,
			"request_headers": json.dumps(_sanitize_headers(request_headers), indent=2) if request_headers else None,
			"request_payload": json.dumps(_sanitize_payload(request_payload), indent=2) if request_payload else None,
			"status_code": status_code,
			"response_body": response_body or None,
			"execution_time_ms": execution_time_ms,
			"is_success": 1 if is_success else 0,
			"error_category": error_category,
			"error_details": error_details,
			"curl_command": curl_command,
		})
		log_doc.insert(ignore_permissions=True)
		frappe.db.commit()
		return log_doc.name
	except Exception as e:
		frappe.log_error(
			f"Failed to create GrayQuest API log for request_id: {request_id}. Error: {e!s}",
			"GrayQuest API Log Creation Error"
		)
		return None


def _generate_curl_command(http_method: str, full_url: str, headers: dict | None = None, payload: dict | None = None) -> str:
	"""Generate a cURL command equivalent to the API request."""
	sensitive_header_keys = ["authorization", "gq-api-key", "api-key", "apikey", "x-api-key"]
	curl_parts = ["curl", "-X", http_method]

	if headers:
		for key, value in headers.items():
			if key.lower() in sensitive_header_keys:
				value = "***REDACTED***"
			curl_parts.append(f'-H "{key}: {value}"')

	if payload and http_method != "GET":
		payload_str = json.dumps(_sanitize_payload(payload), indent=2).replace('"', '\\"')
		curl_parts.append(f'-d "{payload_str}"')

	curl_parts.append(f'"{full_url}"')
	return " \\\n  ".join(curl_parts)


SENSITIVE_HEADER_KEYS = ["authorization", "gq-api-key", "api-key", "apikey", "x-api-key", "token"]
SENSITIVE_PAYLOAD_KEYS = ["key", "hash", "salt", "password", "secret", "token", "api_key", "client_secret"]


def _sanitize_headers(headers: dict | None) -> dict:
	"""Remove sensitive information from headers before logging."""
	if not headers:
		return {}
	sanitized = headers.copy()
	for key in sanitized:
		if key.lower() in SENSITIVE_HEADER_KEYS:
			sanitized[key] = "***REDACTED***"
	return sanitized


def _sanitize_payload(payload: dict | None) -> dict:
	"""Remove sensitive information from payload before logging."""
	if not payload or not isinstance(payload, dict):
		return {}
	sanitized = payload.copy()
	for key in sanitized:
		if key.lower() in SENSITIVE_PAYLOAD_KEYS:
			sanitized[key] = "***REDACTED***"
	return sanitized
