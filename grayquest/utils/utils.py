import re

import frappe
from frappe.utils import flt, get_date_str, get_url


def get_payload(controller, data):
    """
    Constructs the payload for a payment request.
    Optimized to handle both Student/Guardian and Event Ticket flows.

    Args:
        controller: Payment gateway controller
        data (dict): A dictionary containing the reference doctype, reference docname, and amount.

    Returns:
        dict: A dictionary representing the payload for the payment request.
    """
    # Extract doctype and docname from the input data
    doctype = data.get("reference_doctype")
    docname = data.get("reference_docname")

    # Fetch the reference document only once (usually Payment Request)
    ref_doc = frappe.get_doc(doctype, docname)

    # Handle Student Applicant payments via Payment Request
    if hasattr(ref_doc, 'reference_doctype') and ref_doc.reference_doctype == "Student Applicant":
        return _get_student_applicant_payload(controller, ref_doc, data)

    # Handle Ticket payments
    if doctype == "Ticket":
        payload = _get_event_ticket_payload(ref_doc, data)
    else:
        payload = _get_student_payment_payload(controller, ref_doc, data)

    return payload


def _get_event_ticket_payload(ticket_doc, data):
    """
    Constructs payload for Event Ticket payments.
    """
    # Get customer details from ticket
    guardian = frappe.get_doc("Guardian", ticket_doc.customer)
    surl = data.get("success_url")
    furl = data.get("failure_url")
    payload = {
        "student_id": guardian.guardian_name,
        "customer_mobile": guardian.mobile_number or "9999999999",
        "customer_details": get_customer_details(guardian),
        "fee_headers": get_fee_headers(ticket_doc, data),
        "notes": get_notes(ticket_doc, data),
        "udf_details": {"udf_1": ticket_doc.doctype, "udf_2": ticket_doc.name},
        "redirection": {
            "success_url": surl or f"{get_url()}/walsh/events",
            "error_url": furl or f"{get_url()}/walsh/events",
        },
    }
    return payload


def _get_student_payment_payload(controller, ref_doc, data):
    """
    Constructs payload for Student/Guardian payments.
    """
    # Optimize student lookup - use cached values where possible
    redirect_url = None
    if hasattr(ref_doc, "party_type") and hasattr(ref_doc, "party"):
        student = frappe.get_doc(ref_doc.party_type, ref_doc.party)
    elif ref_doc.doctype == "Student Applicant":
        student = ref_doc
        redirect_url = ref_doc.get_payment_redirection_url()
    else:
        student = frappe.get_doc("Student", ref_doc.student)

    # Optimize guardian lookup - batch database calls
    guardian_id = frappe.get_value(
        "Student Guardian", {"parent": student.name}, "guardian"
    )
    customer_details = {}
    customer_mobile = None
    if guardian_id:
        guardian = frappe.get_doc("Guardian", guardian_id)
        customer_details = get_customer_details(guardian)
    elif ref_doc.doctype == "Student Applicant":
        if student.applicant_name:
            customer_details["customer_first_name"] = student.applicant_name
        if student.email_id:
            customer_details["customer_email"] = student.email_id
        if student.mobile:
            customer_mobile = student.mobile.replace("+91-", "").replace("+91", "")
    url = redirect_url or get_url()
    payload = {
        "student_id": student.name,
        "customer_mobile": customer_mobile or student.student_mobile_number or "9999999999",
        "fee_headers": get_fee_headers(ref_doc, data),
        "student_details": get_student_details(controller, student),
        "customer_details": customer_details,
        "notes": get_notes(ref_doc, data),
        "udf_details": {"udf_1": ref_doc.doctype, "udf_2": ref_doc.name},
        "redirection": {
            "success_url": f"{url}/grayquest-payment",
            "error_url": f"{url}/grayquest-payment",
        },
    }
    return payload


def get_student_details(controller, student):
    """
    Constructs the student details dictionary.

    Args:
        student (Document): The student document.

    Returns:
        dict: A dictionary containing the student details.
    """
    # Format date of birth and joining date
    date_of_birth = get_date_str(student.date_of_birth)
    joining_date = student.get("joining_date")
    student_status = student.get("student_status")

    # Fetch program name
    program_name = frappe.get_value("Program", student.program, "program_name")
    sequence = frappe.get_value("Program", student.program, "sequence")

    # Construct the student details dictionary
    student_details = {}
    if student.first_name:
        student_details["student_first_name"] = student.first_name
    if student.middle_name:
        student_details["student_middle_name"] = student.middle_name
    if student.last_name:
        student_details["student_last_name"] = student.last_name
    if date_of_birth:
        student_details["student_dob"] = date_of_birth
    if student.gender:
        student_details["student_gender"] = student.gender.upper()
    if student.student_email_id:
        student_details["student_email"] = student.student_email_id
    if joining_date:
        joining_date = get_date_str(joining_date)
        student_details["student_admission_date"] = joining_date
    if student.blood_group:
        student_details["student_blood_group"] = student.blood_group
    student_details["student_type"] = "NEW" if not student_status or student_status == "New student" else "EXISTING"

    if controller.pass_class_id:
        if program_name.isdigit():
            student_details["student_class_id"] = int(program_name)
        elif sequence:
            student_details["student_class_id"] = int(sequence)
    return student_details


def get_customer_details(guardian):
    """
    Constructs the customer details dictionary.

    Args:
        guardian (Document): The guardian document.

    Returns:
        dict: A dictionary containing the customer details.
    """
    # Construct the customer details dictionary
    customer_details = {}
    if guardian.first_name:
        customer_details["customer_first_name"] = guardian.first_name
    if guardian.middle_name:
        customer_details["customer_middle_name"] = guardian.middle_name
    if guardian.last_name:
        customer_details["customer_last_name"] = guardian.last_name
    if guardian.email_address:
        customer_details["customer_email"] = guardian.email_address
    return customer_details


def get_fee_headers(doc, data):
    """
    Constructs the fee headers dictionary.
    Optimized to handle Event Tickets and other payment types.

    Args:
        doc (Document): The document for which the payment is being made.
        data (dict): Additional data containing amount information.

    Returns:
        dict: A dictionary containing the fee headers.
    """
    doctype_fields = {
        "Fees": ("grand_total", "grand_total"),
        "Fee Advance": ("outstanding_amount", "outstanding_amount"),
        "Event Participant": ("outstanding_amount", "outstanding_amount"),
        "Ticket": ("amount_after_discount", "amount_after_discount"),
        "Student Applicant": ("application_fees", "application_fees"),
        "Instant Fee": ("outstanding_amount", "outstanding_amount"),
    }

    doctype = getattr(doc, "reference_doctype", doc.doctype)

    if doctype in doctype_fields:
        total_field, current_field = doctype_fields[doctype]

        if doctype in ["Event Participant", "Student Applicant"]:
            total = current = getattr(doc, current_field, 0)
        else:
            # For other document types, fetch referenced document
            if hasattr(doc, 'reference_name') and doc.reference_name:
                ref_doc = frappe.get_doc(doctype, doc.reference_name)
                total = getattr(ref_doc, total_field, 0)
                current = getattr(doc, current_field, 0)
            else:
                total = current = getattr(doc, total_field, 0)

        return {"total_payable": flt(total, 2), "current_payable": flt(current, 2)}
    else:
        amount = data.get("amount", 0)
        return {"total_payable": flt(amount, 2), "current_payable": flt(amount, 2)}


def get_notes(doc, data):
    """
    Constructs the notes dictionary.

    Args:
        doc (Document): The document for which the payment is being made.

    Returns:
        dict: A dictionary containing the notes.
    """
    # Construct the notes dictionary
    notes = {}
    if doc.get("remarks"):
        notes["remarks"] = doc.remarks
    if doc.get("posting_date"):
        notes["payment_date"] = get_date_str(doc.posting_date)
    if doc.get("due_date"):
        notes["due_date"] = get_date_str(doc.due_date)
    if data.get("description"):
        description = data.get("description")
        notes["description"] = description.decode('utf-8') if isinstance(description, bytes) else description
    if data.get("reference_doctype"):
        notes["reference_doctype"] = data.get("reference_doctype")
    if data.get("reference_docname"):
        notes["reference_docname"] = data.get("reference_docname")
    return notes


def _get_student_applicant_payload(controller, ref_doc, data):
    """
    Constructs the payload for Student Applicant one-time fee payment.
    This is for Payment Request with reference_doctype = Student Applicant.

    Args:
        controller: GrayQuest settings controller
        ref_doc: Payment Request document
        data: Original kwargs from get_payment_url

    Returns:
        dict: Payload for GrayQuest API
    """
    doctype = data.get("reference_doctype")
    docname = data.get("reference_docname")

    # Fetch the Student Applicant document with existence check
    if not frappe.db.exists("Student Applicant", ref_doc.reference_name):
        frappe.throw(
            f"Student Applicant '{ref_doc.reference_name}' not found for Payment Request '{ref_doc.name}'",
            title="Student Applicant Not Found"
        )
    applicant = frappe.get_doc("Student Applicant", ref_doc.reference_name)

    # Get and clean mobile number
    raw_mobile = applicant.student_mobile_number or applicant.mobile or "9999999999"
    customer_mobile = _clean_mobile_number(raw_mobile)

    # Construct payload
    payload = {
        "student_id": applicant.name,
        "customer_mobile": customer_mobile,
        "fee_headers": {
            "total_payable": flt(applicant.one_time_fee_amount or 0, 2),
            "current_payable": flt(ref_doc.grand_total, 2),
        },
        "student_details": _get_student_applicant_details(controller, applicant),
        "notes": get_notes(ref_doc, data),
        "udf_details": {"udf_1": doctype, "udf_2": docname, "udf_3": "one_time"},
        "redirection": {
            "success_url": f"{get_url()}/grayquest-payment",
            "error_url": f"{get_url()}/grayquest-payment",
        },
    }

    return payload


def _get_student_applicant_details(controller, applicant):
    """
    Constructs student details for Student Applicant.

    Args:
        controller: GrayQuest settings controller
        applicant: Student Applicant document

    Returns:
        dict: Student details for GrayQuest API
    """
    student_details = {}

    # Parse name from full name field
    full_name = applicant.student_name or applicant.applicant_name or ""
    name_parts = full_name.split() if full_name else []
    if name_parts:
        student_details["student_first_name"] = name_parts[0]
        if len(name_parts) > 1:
            student_details["student_last_name"] = " ".join(name_parts[1:])

    # Student Applicant is always NEW
    student_details["student_type"] = "NEW"

    # Date of birth
    if applicant.date_of_birth:
        student_details["student_dob"] = get_date_str(applicant.date_of_birth)

    # Gender
    if applicant.gender:
        student_details["student_gender"] = applicant.gender.upper()

    # Email
    if applicant.email_id:
        student_details["student_email"] = applicant.email_id

    # Program/Class ID
    if controller.pass_class_id and applicant.program:
        program_name = frappe.get_value("Program", applicant.program, "program_name") or applicant.program
        sequence = frappe.get_value("Program", applicant.program, "sequence")
        if program_name and str(program_name).isdigit():
            student_details["student_class_id"] = int(program_name)
        elif sequence:
            student_details["student_class_id"] = int(sequence)

    return student_details


def _clean_mobile_number(mobile):
    """
    Clean mobile number to 10 digits for GrayQuest API.
    Removes country code (+91, 91), dashes, spaces.

    Args:
        mobile: Raw mobile number string

    Returns:
        str: 10-digit mobile number, or default if invalid
    """
    digits = re.sub(r'[^0-9]', '', str(mobile))
    if len(digits) > 10:
        digits = digits[-10:]
    if len(digits) < 10:
        # Invalid mobile number - use default to not block payment flow
        digits = "9999999999"
    return digits
