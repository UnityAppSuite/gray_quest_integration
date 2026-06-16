from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from grayquest.utils.utils import _get_fee_header_for_grade, _get_ticket_fee_headers


def _make_seat(allocated_student, student_grade):
	seat = MagicMock()
	seat.allocated_student = allocated_student
	seat.student_grade = student_grade
	return seat


def _make_grade_detail(grade, payment_gateway, payment_gateway_name, fee_header):
	row = MagicMock()
	row.grade = grade
	row.payment_gateway = payment_gateway
	row.payment_gateway_name = payment_gateway_name
	row.fee_header = fee_header
	return row


def _make_ticket(
	event="EVT-001",
	amount=1300.0,
	payment_gateway_type="GrayQuest Settings",
	payment_gateway_account="TGCH - Event",
	seats=None,
	seat_pricing_total=0.0,
	free_seats_count=0,
	chargeable_seats_count=0,
	name="TKT-TEST-001",
):
	ticket = MagicMock()
	ticket.name = name
	ticket.event = event
	ticket.amount_after_discount = amount
	ticket.payment_gateway_type = payment_gateway_type
	ticket.payment_gateway_account = payment_gateway_account
	ticket.seats = seats or []
	ticket.seat_pricing_total = seat_pricing_total
	ticket.free_seats_count = free_seats_count
	ticket.chargeable_seats_count = chargeable_seats_count
	return ticket


class TestGetFeeHeaderForGrade(FrappeTestCase):
	"""Unit tests for _get_fee_header_for_grade."""

	def _make_event(self, grade_details):
		event = MagicMock()
		event.grade_details = grade_details
		return event

	def test_returns_fee_header_when_grade_and_gateway_match(self):
		event = self._make_event([
			_make_grade_detail(
				grade="Grade 7-TGAA",
				payment_gateway="GrayQuest Settings",
				payment_gateway_name="TGCH - Event",
				fee_header="Walnut_Grade7_Fee",
			)
		])
		result = _get_fee_header_for_grade(
			event, "Grade 7-TGAA", "GrayQuest Settings", "TGCH - Event"
		)
		self.assertEqual(result, "Walnut_Grade7_Fee")

	def test_returns_none_when_grade_does_not_match(self):
		event = self._make_event([
			_make_grade_detail(
				grade="Grade 8-TGAA",
				payment_gateway="GrayQuest Settings",
				payment_gateway_name="TGCH - Event",
				fee_header="Walnut_Grade8_Fee",
			)
		])
		result = _get_fee_header_for_grade(
			event, "Grade 7-TGAA", "GrayQuest Settings", "TGCH - Event"
		)
		self.assertIsNone(result)

	def test_returns_none_when_gateway_does_not_match(self):
		event = self._make_event([
			_make_grade_detail(
				grade="Grade 7-TGAA",
				payment_gateway="Razorpay Merchant Settings",
				payment_gateway_name="TGCH - Event",
				fee_header="Razorpay_Grade7_Fee",
			)
		])
		result = _get_fee_header_for_grade(
			event, "Grade 7-TGAA", "GrayQuest Settings", "TGCH - Event"
		)
		self.assertIsNone(result)

	def test_returns_none_when_grade_details_empty(self):
		event = self._make_event([])
		result = _get_fee_header_for_grade(
			event, "Grade 7-TGAA", "GrayQuest Settings", "TGCH - Event"
		)
		self.assertIsNone(result)


class TestGetTicketFeeHeaders(FrappeTestCase):
	"""Unit tests for _get_ticket_fee_headers — the function that builds the
	fee_headers dict sent to Grayquest."""

	def _make_event(self, grade_details, student_fee=1000.0):
		event = MagicMock()
		event.grade_details = grade_details
		event.student_fee = student_fee
		return event

	@patch("grayquest.utils.utils.frappe")
	def test_single_grade_returns_grade_specific_fee_header(self, mock_frappe):
		"""When all seats belong to one grade and a matching grade_detail row exists,
		fee_headers should use the configured fee_header as the key."""
		seats = [
			_make_seat("STU-001", "Grade 7-TGAA"),
			_make_seat("STU-001", "Grade 7-TGAA"),
		]
		ticket = _make_ticket(seats=seats, amount=1300.0)

		mock_frappe.get_doc.return_value = self._make_event([
			_make_grade_detail(
				grade="Grade 7-TGAA",
				payment_gateway="GrayQuest Settings",
				payment_gateway_name="TGCH - Event",
				fee_header="Walnut_Grade7_Fee",
			)
		])
		mock_frappe.utils.flt = __import__("frappe").utils.flt

		result = _get_ticket_fee_headers(ticket, {})

		self.assertEqual(result, {"Walnut_Grade7_Fee": 1300.0})

	@patch("grayquest.utils.utils.frappe")
	def test_single_grade_falls_back_when_no_matching_grade_detail(self, mock_frappe):
		"""When no grade_detail row matches the grade + gateway combination,
		fee_headers should fall back to total_payable / current_payable."""
		seats = [_make_seat("STU-001", "Grade 7-TGAA")]
		ticket = _make_ticket(seats=seats, amount=1300.0)

		# Event has grade_details for a different gateway
		mock_frappe.get_doc.return_value = self._make_event([
			_make_grade_detail(
				grade="Grade 7-TGAA",
				payment_gateway="Razorpay Merchant Settings",
				payment_gateway_name="TGCH - Event",
				fee_header="Razorpay_Grade7_Fee",
			)
		])
		mock_frappe.utils.flt = __import__("frappe").utils.flt

		result = _get_ticket_fee_headers(ticket, {})

		self.assertEqual(result, {"total_payable": 1300.0, "current_payable": 1300.0})

	@patch("grayquest.utils.utils.frappe")
	def test_falls_back_when_no_seats(self, mock_frappe):
		"""When the ticket has no seats, fee_headers falls back to total_payable / current_payable."""
		ticket = _make_ticket(seats=[], amount=500.0)
		mock_frappe.utils.flt = __import__("frappe").utils.flt

		result = _get_ticket_fee_headers(ticket, {})

		self.assertEqual(result, {"total_payable": 500.0, "current_payable": 500.0})

	@patch("grayquest.utils.utils.frappe")
	def test_multi_grade_returns_per_grade_fee_headers(self, mock_frappe):
		"""When seats span two different grades, each grade should get its own
		fee_header key using the corrected seat/count fields on the Ticket."""
		seats = [
			_make_seat("STU-001", "Grade 7-TGAA"),  # grade 7 student
			_make_seat("STU-002", "Grade 8-TGAA"),  # grade 8 student
		]
		ticket = _make_ticket(
			seats=seats,
			amount=2000.0,
			seat_pricing_total=0.0,
			free_seats_count=2,
			chargeable_seats_count=0,
		)

		event = self._make_event(
			grade_details=[
				_make_grade_detail(
					grade="Grade 7-TGAA",
					payment_gateway="GrayQuest Settings",
					payment_gateway_name="TGCH - Event",
					fee_header="Walnut_Grade7_Fee",
				),
				_make_grade_detail(
					grade="Grade 8-TGAA",
					payment_gateway="GrayQuest Settings",
					payment_gateway_name="TGCH - Event",
					fee_header="Walnut_Grade8_Fee",
				),
			],
			student_fee=1000.0,
		)
		mock_frappe.get_doc.return_value = event
		# Each student has 1 seat allocated
		mock_frappe.db.count.return_value = 1
		mock_frappe.utils.flt = __import__("frappe").utils.flt

		result = _get_ticket_fee_headers(ticket, {})

		# Each grade: 1 student × ₹1000 student_fee + 0 seat charges = ₹1000
		self.assertIn("Walnut_Grade7_Fee", result)
		self.assertIn("Walnut_Grade8_Fee", result)
		self.assertAlmostEqual(result["Walnut_Grade7_Fee"], 1000.0)
		self.assertAlmostEqual(result["Walnut_Grade8_Fee"], 1000.0)

	@patch("grayquest.utils.utils.frappe")
	def test_multi_grade_falls_back_when_no_grade_details_configured(self, mock_frappe):
		"""When multi-grade seats exist but no grade_details rows are configured,
		the result falls back to total_payable / current_payable."""
		seats = [
			_make_seat("STU-001", "Grade 7-TGAA"),
			_make_seat("STU-002", "Grade 8-TGAA"),
		]
		ticket = _make_ticket(
			seats=seats,
			amount=2000.0,
			seat_pricing_total=0.0,
			free_seats_count=2,
			chargeable_seats_count=0,
		)

		mock_frappe.get_doc.return_value = self._make_event(grade_details=[], student_fee=1000.0)
		mock_frappe.db.count.return_value = 1
		mock_frappe.utils.flt = __import__("frappe").utils.flt

		result = _get_ticket_fee_headers(ticket, {})

		self.assertEqual(result, {"total_payable": 2000.0, "current_payable": 2000.0})
