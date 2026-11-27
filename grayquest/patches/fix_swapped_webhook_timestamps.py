import json
from datetime import datetime

import frappe
from frappe.utils import get_datetime

from grayquest.utils import EMI_STATUS_MAPPING
from grayquest.utils.webhook import GQ_TIMESTAMP_FORMAT


def execute():
	"""Fix webhook log and EMI status timestamps stored month-first instead of day-first."""
	for log in frappe.get_all(
		"GrayQuest Webhook Log",
		fields=["name", "data", "event", "timestamp", "reference_doctype", "reference_name"],
	):
		try:
			raw = json.loads(log.data or "{}").get("timestamp")
			correct = datetime.strptime(raw, GQ_TIMESTAMP_FORMAT)
		except (TypeError, ValueError):
			continue

		# Value the old get_datetime parsing stored; only rows still holding it are touched.
		wrong = get_datetime(raw).replace(tzinfo=None)
		if wrong == correct:
			continue

		if log.timestamp == wrong:
			frappe.db.set_value(
				"GrayQuest Webhook Log", log.name, "timestamp", correct, update_modified=False
			)

		status = EMI_STATUS_MAPPING.get(log.event)
		if status and log.reference_doctype and log.reference_name:
			frappe.db.sql(
				"""update `tabGrayQuest EMI Status` set timestamp = %s
				where parenttype = %s and parent = %s and status = %s and timestamp = %s""",
				(correct, log.reference_doctype, log.reference_name, status, wrong),
			)
