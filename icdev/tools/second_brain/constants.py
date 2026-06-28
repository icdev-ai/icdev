# CUI // SP-CTI
"""Second Brain constants — challenge keys, seniority tiers, integration services."""

CHALLENGE_KEYS: list[str] = [
    "meeting_overload",
    "unclear_priorities",
    "context_switching",
    "stakeholder_alignment",
    "information_overload",
    "team_capacity",
    "technical_debt",
    "compliance_burden",
    "delivery_pressure",
    "skill_gaps",
]

CHALLENGE_LABELS: dict[str, str] = {
    "meeting_overload": "Meeting Overload",
    "unclear_priorities": "Unclear Priorities",
    "context_switching": "Context Switching",
    "stakeholder_alignment": "Stakeholder Alignment",
    "information_overload": "Information Overload",
    "team_capacity": "Team Capacity",
    "technical_debt": "Technical Debt",
    "compliance_burden": "Compliance Burden",
    "delivery_pressure": "Delivery Pressure",
    "skill_gaps": "Skill Gaps",
}

SENIORITY_TIERS: list[str] = ["ic", "lead", "manager", "director", "executive"]

SENIORITY_LABELS: dict[str, str] = {
    "ic": "Individual Contributor",
    "lead": "Tech / Team Lead",
    "manager": "Manager",
    "director": "Director",
    "executive": "Executive (VP / C-Suite)",
}

# Integration services supported for onboarding
INTEGRATION_SERVICES: list[str] = [
    "gmail", "gcal", "slack", "github", "gitlab", "jira", "linear", "notion"
]

# Role-to-integration affinity (determines which cards show first per seniority tier)
TIER_INTEGRATIONS: dict[str, list[str]] = {
    "ic":        ["github", "gitlab", "jira", "linear", "slack"],
    "lead":      ["github", "jira", "slack", "gcal", "gmail"],
    "manager":   ["gcal", "gmail", "slack", "jira", "linear"],
    "director":  ["gcal", "gmail", "slack", "jira"],
    "executive": ["gcal", "gmail", "slack"],
}

HORIZON_LABELS: dict[str, str] = {
    "week": "This Week",
    "quarter": "This Quarter",
    "long_term": "Long Term",
}

RELATIONSHIP_TYPES: list[str] = [
    "boss", "direct", "peer", "stakeholder", "customer", "vendor", "other"
]

RELATIONSHIP_LABELS: dict[str, str] = {
    "boss": "My Manager",
    "direct": "Direct Report",
    "peer": "Peer",
    "stakeholder": "Stakeholder",
    "customer": "Customer",
    "vendor": "Vendor / Partner",
    "other": "Other",
}

COMM_STYLE_LABELS: dict[int, str] = {
    1: "Very Direct",
    2: "Direct",
    3: "Balanced",
    4: "Collaborative",
    5: "Very Collaborative",
}

ORG_INDUSTRIES: list[str] = [
    "Aerospace & Defense",
    "Consulting",
    "Education",
    "Energy",
    "Finance & Banking",
    "Government / Public Sector",
    "Healthcare",
    "Information Technology",
    "Legal",
    "Manufacturing",
    "Media & Entertainment",
    "Non-profit",
    "Retail & E-commerce",
    "Telecommunications",
    "Transportation & Logistics",
    "Other",
]

ORG_SIZES: list[str] = [
    "1-10 (startup)",
    "11-50 (small)",
    "51-200 (growing)",
    "201-1000 (mid-size)",
    "1001-5000 (large)",
    "5000+ (enterprise)",
]

FOCUS_BLOCKS: list[str] = ["am", "pm", "none"]

FOCUS_BLOCK_LABELS: dict[str, str] = {
    "am": "Morning (before noon)",
    "pm": "Afternoon (after noon)",
    "none": "No preference",
}

BRIEFING_ENV_FLAG = "ICDEV_SECOND_BRAIN_ENABLED"
