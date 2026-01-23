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