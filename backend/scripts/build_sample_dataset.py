"""Generate `data/question_templates.json` - 200 curated question templates across 5 domains.

Run:  python scripts/build_sample_dataset.py
The output is validated against the domain model, so a malformed row fails fast.
"""

# pylint: disable=too-many-lines  # the module is mostly the 200-question data table

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from ifap.domain.knowledge import QuestionTemplate

OUTPUT = Path(__file__).resolve().parents[1] / "data" / "question_templates.json"

CHOICE_SETS: dict[str, list[str]] = {
    "satisfaction": ["Very Satisfied", "Satisfied", "Neutral", "Unsatisfied", "Very Unsatisfied"],
    "agreement": ["Strongly Agree", "Agree", "Neutral", "Disagree", "Strongly Disagree"],
    "frequency": ["Always", "Often", "Sometimes", "Rarely", "Never"],
    "likelihood": ["Very Likely", "Likely", "Unsure", "Unlikely", "Very Unlikely"],
    "quality": ["Excellent", "Good", "Fair", "Poor", "Very Poor"],
    "ease": ["Very Easy", "Easy", "Neutral", "Difficult", "Very Difficult"],
    "severity": ["None", "Mild", "Moderate", "Severe", "Very Severe"],
    "compliance": ["Fully Compliant", "Partially Compliant", "Non-Compliant", "Not Applicable"],
    "maturity": ["Optimised", "Managed", "Defined", "Repeatable", "Initial"],
    "channel": ["Website", "Mobile App", "Phone", "Email", "In Store", "Social Media"],
    "tenure": ["Less than 1 year", "1-3 years", "3-5 years", "5-10 years", "More than 10 years"],
    "usage": ["Daily", "Weekly", "Monthly", "Rarely", "First time"],
    "features": ["Dashboard", "Reporting", "Integrations", "Mobile Access", "Automation", "Search"],
    "pain": ["None", "Mild", "Moderate", "Severe", "Worst imaginable"],
    "period": ["Within 30 days", "1-3 months", "3-12 months", "Over a year", "Never"],
}


class Row(NamedTuple):
    label: str
    answer_type: str
    category: str
    tags: tuple[str, ...]
    choices: str | None = None
    multi: bool = False
    depends_on: tuple[int, bool | str] | None = None  # (1-based row index, triggering value)
    description: str = ""


class Domain(NamedTuple):
    prefix: str
    template_name: str
    industry: str
    business_function: str
    survey_type: str
    rows: tuple[Row, ...]


CUSTOMER = Domain(
    "cs",
    "Customer Satisfaction",
    "Retail",
    "Customer Experience",
    "customer_satisfaction",
    (
        Row(
            "How satisfied are you with our service overall?",
            "single_choice",
            "Overall Satisfaction",
            ("csat", "overall"),
            "satisfaction",
        ),
        Row(
            "How likely are you to recommend us to a friend or colleague (0-10)?",
            "numeric",
            "Loyalty",
            ("nps", "loyalty"),
        ),
        Row(
            "How likely are you to purchase from us again?",
            "single_choice",
            "Loyalty",
            ("retention", "repurchase"),
            "likelihood",
        ),
        Row(
            "Which channels did you use to interact with us?",
            "multiple_choice",
            "Channels",
            ("omnichannel",),
            "channel",
            True,
        ),
        Row(
            "Did you contact customer support during your last purchase?",
            "boolean",
            "Support",
            ("support",),
        ),
        Row(
            "How satisfied were you with the support you received?",
            "single_choice",
            "Support",
            ("support", "csat"),
            "satisfaction",
            depends_on=(5, True),
        ),
        Row(
            "Was your issue resolved on first contact?",
            "boolean",
            "Support",
            ("fcr", "support"),
            depends_on=(5, True),
        ),
        Row(
            "How easy was it to get your issue resolved?",
            "single_choice",
            "Effort",
            ("ces", "effort"),
            "ease",
        ),
        Row(
            "How would you rate the quality of the product you purchased?",
            "single_choice",
            "Product Quality",
            ("quality",),
            "quality",
        ),
        Row(
            "How would you rate the value for money?",
            "single_choice",
            "Pricing",
            ("pricing", "value"),
            "quality",
        ),
        Row(
            "How satisfied were you with the delivery speed?",
            "single_choice",
            "Delivery",
            ("delivery", "logistics"),
            "satisfaction",
        ),
        Row(
            "Did your order arrive in good condition?",
            "boolean",
            "Delivery",
            ("delivery", "quality"),
        ),
        Row(
            "Please describe what was wrong with your order.",
            "rich_text",
            "Delivery",
            ("delivery", "complaint"),
            depends_on=(12, False),
        ),
        Row(
            "How easy was it to find what you were looking for on our website?",
            "single_choice",
            "Digital Experience",
            ("ux", "web"),
            "ease",
        ),
        Row(
            "How would you rate the checkout process?",
            "single_choice",
            "Digital Experience",
            ("checkout", "ux"),
            "quality",
        ),
        Row(
            "How courteous were our staff?",
            "single_choice",
            "Staff",
            ("staff", "service"),
            "quality",
        ),
        Row(
            "How knowledgeable were our staff about the products?",
            "single_choice",
            "Staff",
            ("staff", "expertise"),
            "quality",
        ),
        Row(
            "How long did you wait before being served (minutes)?",
            "numeric",
            "Wait Time",
            ("wait_time", "operations"),
        ),
        Row(
            "When did you make your most recent purchase?", "date", "Purchase History", ("recency",)
        ),
        Row(
            "How often do you shop with us?",
            "single_choice",
            "Purchase History",
            ("frequency", "engagement"),
            "usage",
        ),
        Row(
            "What is the main reason you chose us over competitors?",
            "single_choice",
            "Competitive Position",
            ("competition",),
            multi=False,
            choices="features_reason",
        ),
        Row(
            "What could we do to improve your experience?",
            "rich_text",
            "Improvement",
            ("open_feedback", "improvement"),
        ),
        Row(
            "What did you like most about your experience?",
            "rich_text",
            "Improvement",
            ("open_feedback", "promoter"),
        ),
        Row("Did you experience any problems with billing?", "boolean", "Billing", ("billing",)),
        Row(
            "Please describe the billing problem you experienced.",
            "rich_text",
            "Billing",
            ("billing", "complaint"),
            depends_on=(24, True),
        ),
        Row(
            "How clear were our return and refund policies?",
            "single_choice",
            "Policies",
            ("returns", "policy"),
            "ease",
        ),
        Row(
            "Have you returned a product to us in the last 6 months?",
            "boolean",
            "Returns",
            ("returns",),
        ),
        Row(
            "How satisfied were you with the return process?",
            "single_choice",
            "Returns",
            ("returns", "csat"),
            "satisfaction",
            depends_on=(27, True),
        ),
        Row(
            "Upload a photo of the damaged item (optional).",
            "file_upload",
            "Delivery",
            ("evidence", "complaint"),
            depends_on=(12, False),
        ),
        Row(
            "How well did our product meet your expectations?",
            "single_choice",
            "Product Quality",
            ("expectations", "quality"),
            "agreement",
        ),
        Row(
            "How satisfied are you with our loyalty program?",
            "single_choice",
            "Loyalty",
            ("loyalty_program",),
            "satisfaction",
        ),
        Row(
            "Are you a member of our loyalty program?",
            "boolean",
            "Loyalty",
            ("loyalty_program", "membership"),
        ),
        Row(
            "How would you rate our communication about your order status?",
            "single_choice",
            "Communication",
            ("communication", "notifications"),
            "quality",
        ),
        Row(
            "How likely are you to leave a positive online review?",
            "single_choice",
            "Advocacy",
            ("advocacy", "reviews"),
            "likelihood",
        ),
        Row(
            "How satisfied are you with the variety of products we offer?",
            "single_choice",
            "Assortment",
            ("assortment",),
            "satisfaction",
        ),
        Row(
            "How would you rate the cleanliness of our store?",
            "single_choice",
            "Store Experience",
            ("store", "facilities"),
            "quality",
        ),
        Row(
            "How satisfied are you with our mobile app?",
            "single_choice",
            "Digital Experience",
            ("mobile", "ux"),
            "satisfaction",
        ),
        Row(
            "How many times did you need to contact us to resolve your issue?",
            "numeric",
            "Effort",
            ("effort", "support"),
        ),
        Row(
            "Did our team follow up with you after your interaction?",
            "boolean",
            "Communication",
            ("follow_up",),
        ),
        Row(
            "Is there anything else you would like to share with us?",
            "rich_text",
            "Improvement",
            ("open_feedback",),
        ),
    ),
)

EMPLOYEE = Domain(
    "ee",
    "Employee Engagement",
    "Cross-Industry",
    "Human Resources",
    "employee_engagement",
    (
        Row(
            "I am proud to work for this organisation.",
            "single_choice",
            "Engagement",
            ("pride", "engagement"),
            "agreement",
        ),
        Row(
            "How likely are you to recommend this organisation as a place to work (0-10)?",
            "numeric",
            "Engagement",
            ("enps",),
        ),
        Row(
            "I see myself still working here in two years.",
            "single_choice",
            "Retention",
            ("retention", "intent_to_stay"),
            "agreement",
        ),
        Row(
            "How long have you worked at the organisation?",
            "single_choice",
            "Demographics",
            ("tenure",),
            "tenure",
        ),
        Row(
            "My manager gives me regular, useful feedback.",
            "single_choice",
            "Management",
            ("feedback", "manager"),
            "agreement",
        ),
        Row(
            "I trust the decisions made by senior leadership.",
            "single_choice",
            "Leadership",
            ("trust", "leadership"),
            "agreement",
        ),
        Row(
            "Leadership communicates the company's direction clearly.",
            "single_choice",
            "Leadership",
            ("communication", "strategy"),
            "agreement",
        ),
        Row(
            "I understand how my work contributes to company goals.",
            "single_choice",
            "Alignment",
            ("purpose", "alignment"),
            "agreement",
        ),
        Row(
            "I have the tools and resources I need to do my job well.",
            "single_choice",
            "Enablement",
            ("enablement", "tools"),
            "agreement",
        ),
        Row(
            "My workload is manageable.",
            "single_choice",
            "Wellbeing",
            ("workload", "burnout"),
            "agreement",
        ),
        Row(
            "How often do you feel stressed at work?",
            "single_choice",
            "Wellbeing",
            ("stress", "burnout"),
            "frequency",
        ),
        Row(
            "I am able to maintain a healthy work-life balance.",
            "single_choice",
            "Wellbeing",
            ("work_life_balance",),
            "agreement",
        ),
        Row(
            "I feel recognised for the work I do.",
            "single_choice",
            "Recognition",
            ("recognition",),
            "agreement",
        ),
        Row(
            "I believe I am paid fairly for my role.",
            "single_choice",
            "Compensation",
            ("pay", "fairness"),
            "agreement",
        ),
        Row(
            "I am satisfied with the benefits offered.",
            "single_choice",
            "Compensation",
            ("benefits",),
            "agreement",
        ),
        Row(
            "There are good opportunities for career growth here.",
            "single_choice",
            "Growth",
            ("career", "development"),
            "agreement",
        ),
        Row(
            "Have you received formal training in the last 12 months?",
            "boolean",
            "Growth",
            ("training", "learning"),
        ),
        Row(
            "How useful was the training you received?",
            "single_choice",
            "Growth",
            ("training", "learning"),
            "quality",
            depends_on=(17, True),
        ),
        Row(
            "What skills would you like to develop next year?",
            "rich_text",
            "Growth",
            ("development", "open_feedback"),
        ),
        Row(
            "My team collaborates effectively.",
            "single_choice",
            "Teamwork",
            ("collaboration", "team"),
            "agreement",
        ),
        Row(
            "I feel comfortable voicing a contrary opinion.",
            "single_choice",
            "Psychological Safety",
            ("psych_safety", "voice"),
            "agreement",
        ),
        Row(
            "People here are treated fairly regardless of background.",
            "single_choice",
            "Inclusion",
            ("dei", "fairness"),
            "agreement",
        ),
        Row(
            "I feel a sense of belonging at work.",
            "single_choice",
            "Inclusion",
            ("belonging", "dei"),
            "agreement",
        ),
        Row(
            "Have you experienced or witnessed discrimination at work in the last year?",
            "boolean",
            "Inclusion",
            ("dei", "ethics"),
        ),
        Row(
            "Would you like HR to contact you confidentially about this?",
            "boolean",
            "Inclusion",
            ("dei", "escalation"),
            depends_on=(24, True),
        ),
        Row(
            "Which working arrangement do you currently have?",
            "single_choice",
            "Work Arrangement",
            ("hybrid", "remote"),
            "work_mode",
        ),
        Row(
            "How satisfied are you with your current working arrangement?",
            "single_choice",
            "Work Arrangement",
            ("hybrid", "remote"),
            "satisfaction",
        ),
        Row(
            "My manager supports my professional development.",
            "single_choice",
            "Management",
            ("manager", "development"),
            "agreement",
        ),
        Row(
            "How often do you have one-to-one meetings with your manager?",
            "single_choice",
            "Management",
            ("manager", "cadence"),
            "frequency",
        ),
        Row(
            "I would recommend my manager to others.",
            "single_choice",
            "Management",
            ("manager", "mnps"),
            "agreement",
        ),
        Row(
            "Change is managed well in this organisation.",
            "single_choice",
            "Change Management",
            ("change",),
            "agreement",
        ),
        Row(
            "Cross-team processes help rather than hinder my work.",
            "single_choice",
            "Enablement",
            ("process", "bureaucracy"),
            "agreement",
        ),
        Row(
            "Have you considered leaving the organisation in the last 6 months?",
            "boolean",
            "Retention",
            ("attrition_risk",),
        ),
        Row(
            "What is the main reason you have considered leaving?",
            "rich_text",
            "Retention",
            ("attrition_risk", "open_feedback"),
            depends_on=(33, True),
        ),
        Row(
            "How energised do you feel by your work?",
            "single_choice",
            "Engagement",
            ("energy", "engagement"),
            "frequency",
        ),
        Row(
            "I know what is expected of me at work.",
            "single_choice",
            "Alignment",
            ("clarity", "role"),
            "agreement",
        ),
        Row(
            "How many hours do you typically work per week?",
            "numeric",
            "Wellbeing",
            ("workload", "hours"),
        ),
        Row(
            "When did you last have a performance review?",
            "date",
            "Performance",
            ("performance_review",),
        ),
        Row(
            "The performance review process is fair and transparent.",
            "single_choice",
            "Performance",
            ("performance_review", "fairness"),
            "agreement",
        ),
        Row(
            "What one thing would most improve your experience at work?",
            "rich_text",
            "Improvement",
            ("open_feedback",),
        ),
    ),
)

HEALTHCARE = Domain(
    "hc",
    "Healthcare Assessment",
    "Healthcare",
    "Clinical Intake",
    "healthcare_assessment",
    (
        Row("What is your date of birth?", "date", "Demographics", ("patient_identity",)),
        Row(
            "What is the main reason for your visit today?",
            "rich_text",
            "Chief Complaint",
            ("chief_complaint",),
        ),
        Row("When did your symptoms start?", "date", "Chief Complaint", ("onset",)),
        Row("Are you currently experiencing pain?", "boolean", "Pain Assessment", ("pain",)),
        Row(
            "On a scale of 0-10, how severe is your pain?",
            "numeric",
            "Pain Assessment",
            ("pain", "severity"),
            depends_on=(4, True),
        ),
        Row(
            "How would you describe your pain?",
            "multiple_choice",
            "Pain Assessment",
            ("pain",),
            "pain_type",
            True,
            depends_on=(4, True),
        ),
        Row(
            "Do you have any known allergies to medication?",
            "boolean",
            "Allergies",
            ("allergies", "safety"),
        ),
        Row(
            "Please list your medication allergies and reactions.",
            "rich_text",
            "Allergies",
            ("allergies", "safety"),
            depends_on=(7, True),
        ),
        Row(
            "Are you currently taking any prescription medication?",
            "boolean",
            "Medications",
            ("medications",),
        ),
        Row(
            "Please list your current medications and dosages.",
            "rich_text",
            "Medications",
            ("medications",),
            depends_on=(9, True),
        ),
        Row(
            "Which of the following conditions have you been diagnosed with?",
            "multiple_choice",
            "Medical History",
            ("history", "chronic"),
            "conditions",
            True,
        ),
        Row(
            "Have you had any surgeries in the past?",
            "boolean",
            "Medical History",
            ("surgical_history",),
        ),
        Row(
            "Please describe your previous surgeries and dates.",
            "rich_text",
            "Medical History",
            ("surgical_history",),
            depends_on=(12, True),
        ),
        Row(
            "Do you smoke or use tobacco products?",
            "boolean",
            "Lifestyle",
            ("smoking", "risk_factor"),
        ),
        Row(
            "How many cigarettes do you smoke per day?",
            "numeric",
            "Lifestyle",
            ("smoking", "risk_factor"),
            depends_on=(14, True),
        ),
        Row(
            "How often do you consume alcohol?",
            "single_choice",
            "Lifestyle",
            ("alcohol", "risk_factor"),
            "frequency",
        ),
        Row(
            "How many days per week do you exercise for at least 30 minutes?",
            "numeric",
            "Lifestyle",
            ("exercise",),
        ),
        Row(
            "Is there a family history of heart disease?",
            "boolean",
            "Family History",
            ("family_history", "cardiac"),
        ),
        Row(
            "Is there a family history of cancer?",
            "boolean",
            "Family History",
            ("family_history", "oncology"),
        ),
        Row(
            "Is there a family history of diabetes?",
            "boolean",
            "Family History",
            ("family_history", "diabetes"),
        ),
        Row(
            "How would you rate your overall health?",
            "single_choice",
            "General Health",
            ("self_rated_health",),
            "quality",
        ),
        Row(
            "Over the past two weeks, how often have you felt down or hopeless?",
            "single_choice",
            "Mental Health",
            ("phq2", "depression"),
            "frequency",
        ),
        Row(
            "Over the past two weeks, how often have you had little interest in doing things?",
            "single_choice",
            "Mental Health",
            ("phq2", "depression"),
            "frequency",
        ),
        Row(
            "How often do you feel anxious or on edge?",
            "single_choice",
            "Mental Health",
            ("gad", "anxiety"),
            "frequency",
        ),
        Row(
            "How many hours of sleep do you get on average per night?",
            "numeric",
            "Sleep",
            ("sleep",),
        ),
        Row(
            "Do you have difficulty falling or staying asleep?",
            "boolean",
            "Sleep",
            ("sleep", "insomnia"),
        ),
        Row(
            "Are you currently pregnant or could you be pregnant?",
            "boolean",
            "Reproductive Health",
            ("pregnancy", "safety"),
        ),
        Row(
            "When was your last menstrual period?",
            "date",
            "Reproductive Health",
            ("pregnancy",),
            depends_on=(27, True),
        ),
        Row(
            "Have you received all recommended vaccinations?",
            "boolean",
            "Immunisation",
            ("vaccination",),
        ),
        Row(
            "Upload a copy of your vaccination record (optional).",
            "file_upload",
            "Immunisation",
            ("vaccination", "documents"),
        ),
        Row(
            "Upload any recent test results or referral letters.",
            "file_upload",
            "Documents",
            ("documents", "referral"),
        ),
        Row("What is your height in centimetres?", "numeric", "Vitals", ("vitals", "bmi")),
        Row("What is your weight in kilograms?", "numeric", "Vitals", ("vitals", "bmi")),
        Row(
            "Have you experienced shortness of breath recently?",
            "boolean",
            "Symptoms",
            ("respiratory", "triage"),
        ),
        Row("Have you had a fever in the last 7 days?", "boolean", "Symptoms", ("fever", "triage")),
        Row(
            "Do you require an interpreter for your appointment?",
            "boolean",
            "Accessibility",
            ("accessibility", "language"),
        ),
        Row(
            "Do you have any mobility or accessibility needs?",
            "rich_text",
            "Accessibility",
            ("accessibility",),
        ),
        Row(
            "Who should we contact in an emergency?",
            "rich_text",
            "Emergency Contact",
            ("emergency_contact",),
        ),
        Row(
            "Do you consent to sharing your records with your primary care provider?",
            "boolean",
            "Consent",
            ("consent", "privacy"),
        ),
        Row(
            "How satisfied were you with the intake process today?",
            "single_choice",
            "Patient Experience",
            ("patient_experience",),
            "satisfaction",
        ),
    ),
)

PRODUCT = Domain(
    "pf",
    "Product Feedback",
    "Technology",
    "Product Management",
    "product_feedback",
    (
        Row(
            "How often do you use the product?",
            "single_choice",
            "Usage",
            ("usage", "engagement"),
            "usage",
        ),
        Row(
            "Which features do you use most?",
            "multiple_choice",
            "Feature Usage",
            ("features",),
            "features",
            True,
        ),
        Row(
            "How easy is the product to use?",
            "single_choice",
            "Usability",
            ("usability", "ux"),
            "ease",
        ),
        Row(
            "How satisfied are you with the product overall?",
            "single_choice",
            "Overall Satisfaction",
            ("csat",),
            "satisfaction",
        ),
        Row("How likely are you to recommend the product (0-10)?", "numeric", "Loyalty", ("nps",)),
        Row(
            "How would you feel if you could no longer use the product?",
            "single_choice",
            "Product-Market Fit",
            ("pmf", "sean_ellis"),
            "pmf",
        ),
        Row(
            "What is the main benefit you get from the product?",
            "rich_text",
            "Value",
            ("value_prop",),
        ),
        Row(
            "What problem were you trying to solve when you started using the product?",
            "rich_text",
            "Jobs To Be Done",
            ("jtbd", "discovery"),
        ),
        Row(
            "How well does the product solve that problem?",
            "single_choice",
            "Jobs To Be Done",
            ("jtbd",),
            "quality",
        ),
        Row(
            "Have you encountered any bugs in the last month?",
            "boolean",
            "Quality",
            ("bugs", "reliability"),
        ),
        Row(
            "Please describe the bug you encountered.",
            "rich_text",
            "Quality",
            ("bugs",),
            depends_on=(10, True),
        ),
        Row(
            "Upload a screenshot of the issue (optional).",
            "file_upload",
            "Quality",
            ("bugs", "evidence"),
            depends_on=(10, True),
        ),
        Row(
            "How would you rate the product's performance and speed?",
            "single_choice",
            "Performance",
            ("performance",),
            "quality",
        ),
        Row(
            "How reliable has the product been for you?",
            "single_choice",
            "Quality",
            ("reliability", "uptime"),
            "quality",
        ),
        Row(
            "Which feature would you most like us to build next?",
            "rich_text",
            "Roadmap",
            ("roadmap", "feature_request"),
        ),
        Row(
            "How important is a mobile app to you?",
            "single_choice",
            "Roadmap",
            ("mobile", "prioritisation"),
            "importance",
        ),
        Row(
            "How satisfied are you with the product's integrations?",
            "single_choice",
            "Integrations",
            ("integrations",),
            "satisfaction",
        ),
        Row(
            "Which tools would you like us to integrate with?",
            "rich_text",
            "Integrations",
            ("integrations", "feature_request"),
        ),
        Row(
            "How easy was it to get started with the product?",
            "single_choice",
            "Onboarding",
            ("onboarding", "activation"),
            "ease",
        ),
        Row(
            "How long did it take before the product delivered value (days)?",
            "numeric",
            "Onboarding",
            ("time_to_value",),
        ),
        Row(
            "Did you use our documentation or help centre?",
            "boolean",
            "Support",
            ("docs", "self_service"),
        ),
        Row(
            "How helpful was the documentation?",
            "single_choice",
            "Support",
            ("docs",),
            "quality",
            depends_on=(21, True),
        ),
        Row(
            "How would you rate the product's design and look?",
            "single_choice",
            "Design",
            ("ui", "design"),
            "quality",
        ),
        Row(
            "How fair is the product's pricing for the value you receive?",
            "single_choice",
            "Pricing",
            ("pricing", "value"),
            "agreement",
        ),
        Row(
            "Which pricing plan are you on?",
            "single_choice",
            "Pricing",
            ("pricing", "plan"),
            "plan",
        ),
        Row("Are you considering switching to a competitor?", "boolean", "Churn Risk", ("churn",)),
        Row(
            "Which competitor are you considering and why?",
            "rich_text",
            "Churn Risk",
            ("churn", "competition"),
            depends_on=(26, True),
        ),
        Row(
            "How well does the product meet your security requirements?",
            "single_choice",
            "Security",
            ("security", "trust"),
            "quality",
        ),
        Row("What is your role?", "single_choice", "Demographics", ("persona",), "role"),
        Row(
            "How many people in your organisation use the product?",
            "numeric",
            "Demographics",
            ("seats", "firmographics"),
        ),
        Row("When did you first start using the product?", "date", "Usage", ("tenure",)),
        Row(
            "How satisfied are you with the reporting capabilities?",
            "single_choice",
            "Feature Usage",
            ("reporting", "analytics"),
            "satisfaction",
        ),
        Row(
            "How satisfied are you with search within the product?",
            "single_choice",
            "Feature Usage",
            ("search",),
            "satisfaction",
        ),
        Row(
            "Did the latest release improve your experience?",
            "boolean",
            "Release Feedback",
            ("release",),
        ),
        Row(
            "What did you think of the latest release?",
            "rich_text",
            "Release Feedback",
            ("release", "open_feedback"),
        ),
        Row(
            "How accessible is the product for users with disabilities?",
            "single_choice",
            "Accessibility",
            ("a11y",),
            "quality",
        ),
        Row(
            "Would you be willing to join a user research session?",
            "boolean",
            "Research",
            ("research_panel",),
        ),
        Row(
            "How responsive is our team to your feedback?",
            "single_choice",
            "Customer Success",
            ("feedback_loop",),
            "quality",
        ),
        Row(
            "What is the one thing we should stop doing?",
            "rich_text",
            "Improvement",
            ("open_feedback",),
        ),
        Row(
            "Is there anything else you would like to tell the product team?",
            "rich_text",
            "Improvement",
            ("open_feedback",),
        ),
    ),
)

COMPLIANCE = Domain(
    "cr",
    "Compliance Review",
    "Financial Services",
    "Compliance & Risk",
    "compliance_review",
    (
        Row(
            "Does the organisation have a documented information security policy?",
            "boolean",
            "Governance",
            ("iso27001", "policy"),
        ),
        Row(
            "When was the information security policy last reviewed?",
            "date",
            "Governance",
            ("iso27001", "policy"),
            depends_on=(1, True),
        ),
        Row(
            "Upload the current information security policy.",
            "file_upload",
            "Governance",
            ("evidence", "policy"),
            depends_on=(1, True),
        ),
        Row(
            "Is there a named owner accountable for compliance?",
            "boolean",
            "Governance",
            ("accountability",),
        ),
        Row(
            "How would you rate the maturity of your compliance programme?",
            "single_choice",
            "Governance",
            ("maturity",),
            "maturity",
        ),
        Row(
            "Is access to sensitive systems granted on a least-privilege basis?",
            "single_choice",
            "Access Control",
            ("iam", "soc2"),
            "compliance",
        ),
        Row(
            "How often are user access rights reviewed?",
            "single_choice",
            "Access Control",
            ("iam", "access_review"),
            "review_period",
        ),
        Row(
            "Is multi-factor authentication enforced for all privileged accounts?",
            "boolean",
            "Access Control",
            ("mfa", "iam"),
        ),
        Row(
            "Are terminated users' accounts disabled within 24 hours?",
            "single_choice",
            "Access Control",
            ("offboarding", "iam"),
            "compliance",
        ),
        Row(
            "Is customer data encrypted at rest?",
            "boolean",
            "Data Protection",
            ("encryption", "gdpr"),
        ),
        Row(
            "Is customer data encrypted in transit?",
            "boolean",
            "Data Protection",
            ("encryption", "tls"),
        ),
        Row(
            "Does the organisation maintain a record of processing activities (GDPR Art. 30)?",
            "single_choice",
            "Data Protection",
            ("gdpr", "ropa"),
            "compliance",
        ),
        Row(
            "How many data subject access requests were received in the last 12 months?",
            "numeric",
            "Data Protection",
            ("gdpr", "dsar"),
        ),
        Row(
            "Is there a defined data retention schedule?",
            "boolean",
            "Data Protection",
            ("retention", "gdpr"),
        ),
        Row(
            "Has a data protection impact assessment been completed for high-risk processing?",
            "single_choice",
            "Data Protection",
            ("dpia", "gdpr"),
            "compliance",
        ),
        Row(
            "Has the organisation experienced a data breach in the last 24 months?",
            "boolean",
            "Incident Management",
            ("breach", "incident"),
        ),
        Row(
            "Describe the breach and the remediation actions taken.",
            "rich_text",
            "Incident Management",
            ("breach", "remediation"),
            depends_on=(16, True),
        ),
        Row(
            "Was the regulator notified within the required timeframe?",
            "boolean",
            "Incident Management",
            ("breach", "regulatory_reporting"),
            depends_on=(16, True),
        ),
        Row(
            "Is there a documented incident response plan?",
            "boolean",
            "Incident Management",
            ("irp",),
        ),
        Row(
            "When was the incident response plan last tested?",
            "date",
            "Incident Management",
            ("irp", "testing"),
            depends_on=(19, True),
        ),
        Row(
            "Are third-party vendors assessed for security risk before onboarding?",
            "single_choice",
            "Third-Party Risk",
            ("vendor_risk", "tprm"),
            "compliance",
        ),
        Row(
            "How many critical vendors does the organisation rely on?",
            "numeric",
            "Third-Party Risk",
            ("vendor_risk",),
        ),
        Row(
            "Do vendor contracts include right-to-audit clauses?",
            "boolean",
            "Third-Party Risk",
            ("vendor_risk", "contracts"),
        ),
        Row(
            "Are anti-money laundering (AML) checks performed on new customers?",
            "single_choice",
            "Financial Crime",
            ("aml", "kyc"),
            "compliance",
        ),
        Row(
            "Is there a process for reporting suspicious activity?",
            "boolean",
            "Financial Crime",
            ("sar", "aml"),
        ),
        Row(
            "How often do employees complete compliance training?",
            "single_choice",
            "Training",
            ("training", "awareness"),
            "review_period",
        ),
        Row(
            "What percentage of staff completed mandatory compliance training this year?",
            "numeric",
            "Training",
            ("training", "kpi"),
        ),
        Row(
            "Is there a whistleblowing channel available to all employees?",
            "boolean",
            "Ethics",
            ("whistleblowing", "ethics"),
        ),
        Row(
            "Is there a documented business continuity plan?",
            "boolean",
            "Resilience",
            ("bcp", "dora"),
        ),
        Row("When was disaster recovery last tested?", "date", "Resilience", ("dr", "testing")),
        Row(
            "What is the recovery time objective for critical systems (hours)?",
            "numeric",
            "Resilience",
            ("rto", "dr"),
        ),
        Row(
            "Are system changes approved through a formal change-management process?",
            "single_choice",
            "Change Management",
            ("change_control", "sox"),
            "compliance",
        ),
        Row(
            "Are audit logs retained and protected from tampering?",
            "single_choice",
            "Logging & Monitoring",
            ("audit_logs", "soc2"),
            "compliance",
        ),
        Row(
            "How long are security logs retained (days)?",
            "numeric",
            "Logging & Monitoring",
            ("audit_logs", "retention"),
        ),
        Row(
            "Were any findings raised in the most recent external audit?",
            "boolean",
            "Audit",
            ("audit_findings",),
        ),
        Row(
            "List the open audit findings and target remediation dates.",
            "rich_text",
            "Audit",
            ("audit_findings", "remediation"),
            depends_on=(35, True),
        ),
        Row(
            "Upload the most recent external audit report.",
            "file_upload",
            "Audit",
            ("evidence", "audit"),
        ),
        Row(
            "Which regulatory frameworks apply to the organisation?",
            "multiple_choice",
            "Regulatory Scope",
            ("scope",),
            "frameworks",
            True,
        ),
        Row(
            "Are vulnerability scans performed at least quarterly?",
            "single_choice",
            "Vulnerability Management",
            ("vuln_mgmt", "pci"),
            "compliance",
        ),
        Row(
            "Describe any compliance gaps you are aware of.",
            "rich_text",
            "Self Assessment",
            ("gaps", "open_feedback"),
        ),
    ),
)

EXTRA_CHOICES: dict[str, list[str]] = {
    "features_reason": [
        "Price",
        "Quality",
        "Convenience",
        "Brand Reputation",
        "Recommendation",
        "Customer Service",
    ],
    "work_mode": ["Fully Remote", "Hybrid", "Fully On-site"],
    "pain_type": ["Sharp", "Dull", "Throbbing", "Burning", "Aching", "Stabbing"],
    "conditions": [
        "Diabetes",
        "Hypertension",
        "Asthma",
        "Heart Disease",
        "Arthritis",
        "None of the above",
    ],
    "pmf": ["Very Disappointed", "Somewhat Disappointed", "Not Disappointed"],
    "importance": ["Critical", "Important", "Nice to Have", "Not Important"],
    "plan": ["Free", "Starter", "Professional", "Enterprise"],
    "role": ["Executive", "Manager", "Individual Contributor", "Developer", "Analyst", "Other"],
    "review_period": ["Monthly", "Quarterly", "Annually", "Ad hoc", "Never"],
    "frameworks": ["GDPR", "SOX", "PCI DSS", "ISO 27001", "SOC 2", "DORA", "HIPAA"],
}

ALL_DOMAINS: tuple[Domain, ...] = (CUSTOMER, EMPLOYEE, HEALTHCARE, PRODUCT, COMPLIANCE)


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")


def _choices(key: str | None) -> list[dict[str, str]]:
    if key is None:
        return []
    labels = CHOICE_SETS.get(key) or EXTRA_CHOICES[key]
    return [{"value": _slug(label), "label": label} for label in labels]


def _validations(row: Row) -> list[dict[str, object]]:
    rules: list[dict[str, object]] = [{"kind": "required", "value": row.depends_on is None}]
    if row.answer_type == "numeric":
        rules.append({"kind": "min_value", "value": 0})
    if row.answer_type == "rich_text":
        rules.append({"kind": "max_length", "value": 2000})
    if row.answer_type == "file_upload":
        rules.append({"kind": "allowed_file_types", "value": ["pdf", "png", "jpg"]})
        rules.append({"kind": "max_file_size_mb", "value": 10})
    return rules


def _record(domain: Domain, index: int, row: Row) -> dict[str, object]:
    answer_type = "multiple_choice" if row.multi else row.answer_type
    dependency_rules: list[dict[str, object]] = []
    if row.depends_on is not None:
        parent, value = row.depends_on
        dependency_rules.append(
            {
                "depends_on": f"{domain.prefix}-{parent:03d}",
                "operator": "equals",
                "value": value,
                "action": "show",
            }
        )
    return {
        "template_id": f"{domain.prefix}-{index:03d}",
        "template_name": domain.template_name,
        "question": {
            "id": f"{domain.prefix}-{index:03d}",
            "label": row.label,
            "category": row.category,
            "description": row.description or f"{domain.template_name} - {row.category}",
            "answer_type": answer_type,
            "choices": _choices(row.choices),
            "validations": _validations(row),
            "dependency_rules": dependency_rules,
            "business_tags": list(row.tags),
        },
        "metadata": {
            "industry": domain.industry,
            "business_function": domain.business_function,
            "survey_type": domain.survey_type,
            "question_tags": list(row.tags),
        },
    }


def build() -> list[dict[str, object]]:
    records = [
        _record(domain, index, row)
        for domain in ALL_DOMAINS
        for index, row in enumerate(domain.rows, start=1)
    ]
    for record in records:  # fail fast on schema drift
        QuestionTemplate.model_validate(record)
    return records


def main() -> None:
    records = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(records)} templates to {OUTPUT}")


if __name__ == "__main__":
    main()
