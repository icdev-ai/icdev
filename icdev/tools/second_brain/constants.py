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

CHALLENGE_DESCRIPTIONS: dict[str, str] = {
    "meeting_overload": "Too many meetings leave little time for deep, focused work.",
    "unclear_priorities": "Competing demands make it hard to know what matters most right now.",
    "context_switching": "Constant task-switching fragments concentration and reduces output quality.",
    "stakeholder_alignment": "Different teams or leaders have diverging expectations or priorities.",
    "information_overload": "More signals arrive than you can usefully process — noise drowns signal.",
    "team_capacity": "Not enough people, time, or budget to deliver what's expected.",
    "technical_debt": "Accumulated shortcuts and legacy decisions slow down every new initiative.",
    "compliance_burden": "Regulatory, audit, or policy requirements consume disproportionate time.",
    "delivery_pressure": "Hard deadlines or executive pressure compress quality and planning cycles.",
    "skill_gaps": "The team (or you) lack skills the current work demands.",
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
    "peer": "Peer / Colleague",
    "stakeholder": "Internal Customer",
    "customer": "External Customer",
    "vendor": "Partner / Vendor",
    "other": "Other",
}

# Customer type icons for UI display
CUSTOMER_TYPE_ICONS: dict[str, str] = {
    "customer":    "🏢",
    "stakeholder": "🏠",
    "boss":        "👆",
    "direct":      "👥",
    "peer":        "🤝",
    "vendor":      "🔗",
    "other":       "📎",
}

# Tooltip descriptions for each customer / relationship type
CUSTOMER_TYPE_DESCRIPTIONS: dict[str, str] = {
    "customer": (
        "Organizations or groups outside your company that consume your services. "
        "For network architects: CSPs, ISPs, enterprise clients who rely on the infrastructure you design."
    ),
    "stakeholder": (
        "Teams inside your organization whose daily work depends on what you deliver. "
        "For network architects: Network Operations, Application Teams, Security, Cloud Engineering."
    ),
    "boss": (
        "Your management chain and sponsors who set strategic direction and evaluate your impact."
    ),
    "direct": (
        "People you lead and are responsible for developing — your direct reports or team members."
    ),
    "peer": (
        "Same-level collaborators in adjacent domains. "
        "Peers often share resources, coordinate dependencies, and are your primary cross-functional partners."
    ),
    "vendor": (
        "Technology partners, vendors, and contractors whose products or services you evaluate, integrate, or manage."
    ),
    "other": "Any other key relationship that doesn't fit the categories above.",
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
