# Copyright (c) 2024, Hybrowlabs Technologies and Contributors
# See license.txt

from datetime import datetime
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from grayquest.utils.webhook import parse_gq_timestamp


class TestGrayQuestWebhookLog(FrappeTestCase):
	def test_ambiguous_day_is_not_swapped_with_month(self):
		# Real payload: emi.process.completed received on 2 Sep 2026
		self.assertEqual(parse_gq_timestamp("02-09-2026 20:54:04"), datetime(2026, 9, 2, 20, 54, 4))

	def test_unambiguous_day(self):
		self.assertEqual(parse_gq_timestamp("24-08-2026 21:38:14"), datetime(2026, 8, 24, 21, 38, 14))

	def test_leap_day(self):
		self.assertEqual(parse_gq_timestamp("29-02-2028 00:00:00"), datetime(2028, 2, 29))

	@patch("grayquest.utils.webhook.frappe.log_error")
	def test_malformed_falls_back_to_now_and_logs(self, log_error):
		for value in ("2026-13-45", "31-02-2026 10:00:00", 12345):
			before = datetime.now()
			self.assertGreaterEqual(parse_gq_timestamp(value), before.replace(microsecond=0))
		self.assertEqual(log_error.call_count, 3)

	def test_missing_uses_now(self):
		before = datetime.now()
		self.assertGreaterEqual(parse_gq_timestamp(None), before.replace(microsecond=0))
