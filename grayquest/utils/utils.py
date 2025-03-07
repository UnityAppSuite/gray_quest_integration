import frappe
from frappe.utils import get_url, get_date_str


def get_payload(controller, data):
    """
    Constructs the payload for a payment request.

    Args:
        data (dict): A dictionary containing the reference doctype, reference docname, and amount.

    Returns:
        dict: A dictionary representing the payload for the payment request.
    """
    # Extract doctype and docname from the input data
    doctype = data.get("reference_doctype")
    docname = data.get("reference_docname")

    # Fetch the reference document
    ref_doc = frappe.get_doc(doctype, docname)

    # Fetch the student document associated with the reference document
    if hasattr(ref_doc, "party_type") and hasattr(ref_doc, "party"):
        student = frappe.get_doc(ref_doc.party_type, ref_doc.party)
    else:
        student = frappe.get_doc("Student", ref_doc.student)

    # Fetch the guardian ID and document associated with the student
    guardian_id = frappe.get_value(
        "Student Guardian", {"parent": student.name}, "guardian"
    )
    guardian = frappe.get_doc("Guardian", guardian_id)

    # Construct the payload dictionary
    payload = {
        "student_id": student.name,
        "customer_mobile": student.student_mobile_number or "9999999999",
        "fee_headers": get_fee_headers(ref_doc),
        "student_details": get_student_details(controller, student),
        "customer_details": get_customer_details(guardian),
        "notes": get_notes(ref_doc, data),
        "udf_details": {"udf_1": doctype, "udf_2": docname},
        "redirection": {
            "success_url": f"{get_url()}/grayquest-payment",
            "error_url": f"{get_url()}/grayquest-payment",
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
    joining_date = get_date_str(student.joining_date)

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
    if student.student_status == "New student":
        student_details["student_type"] = "NEW"
    else:
        student_details["student_type"] = "EXISTING"
    if date_of_birth:
        student_details["student_dob"] = date_of_birth
    if student.gender:
        student_details["student_gender"] = student.gender.upper()
    if student.student_email_id:
        student_details["student_email"] = student.student_email_id
    if joining_date:
        student_details["student_admission_date"] = joining_date
    if student.blood_group:
        student_details["student_blood_group"] = student.blood_group

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


def get_fee_headers(doc):
    """
    Constructs the fee headers dictionary.

    Args:
        doc (Document): The document for which the payment is being made.

    Returns:
        dict: A dictionary containing the fee headers.
    """
    doctype_fields = {
        "Fees": ("grand_total", "grand_total"),
        "Fee Advance": ("outstanding_amount", "outstanding_amount"),
        "Event Participant": ("outstanding_amount", "outstanding_amount")
    }

    doctype = getattr(doc, "reference_doctype", doc.doctype)
    
    if doctype in doctype_fields:
        total_field, current_field = doctype_fields[doctype]

        if doctype == "Event Participant":
            total = current = getattr(doc, current_field, 0)
        else:
            ref_doc = frappe.get_doc(doctype, doc.reference_name)
            total = getattr(ref_doc, total_field, 0)
            current = getattr(doc, current_field, 0)

        return {"total_payable": total, "current_payable": current}

    return {"total_payable": 0, "current_payable": 0}


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
