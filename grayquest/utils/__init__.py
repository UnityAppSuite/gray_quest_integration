from grayquest.utils.utils import (
    get_payload,
    get_student_details,
    get_customer_details,
    get_fees_payload,
    get_applicant_payload_direct,
)

EMI_STATUS_MAPPING = {
    "emi.form.submitted": "Form Submitted",
    "emi.approved": "Approved",
    "emi.downpayment.captured": "Downpayment Captured",
    "emi.process.completed": "Process Completed",
    "emi.disbursed": "Disbursed",
    "emi.installment.captured": "Installment Captured",
    "emi.installment.overdue": "Installment Overdue",
    "emi.closed": "Closed",
    "emi.backout": "Backout",
    "emi.rejected": "Rejected",
}

# An EMI application only blocks payment while it is genuinely being processed.
# Everything outside this set is terminal - note that "emi.process.completed"
# means the process finished, so it must not block.
EMI_IN_FLIGHT_EVENTS = {
    "emi.form.submitted",
    "emi.approved",
    "emi.downpayment.captured",
}

# The event on which GrayQuest hands over the money. The installment it names is
# EMI funded from here on, whether or not the settlement that follows succeeds.
EMI_FUNDED_EVENTS = {
    "emi.disbursed",
}

# Events after which the application can no longer fund anything.
EMI_UNSUCCESSFUL_EVENTS = {
    "emi.rejected",
    "emi.backout",
}