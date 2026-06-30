"""
config/constants.py
====================
Static, immutable constants for the NPMAI Agentic World simulation.

This module defines every enum, lookup table, and constant dictionary used
across the simulation: agent lifecycle states, reproduction/mutation
taxonomy, the 100-class npmai_agents tool registry (mapped to a fixed
bit-index for the capability_chromosome genome segment), credit economy
costs, mutation rates, memory limits, and core world-clock constants.

Nothing in this module performs I/O or depends on any other project module.
It is safe to import from anywhere, including before logging/DB are wired up.
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
from enum import Enum, unique


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------

@unique
class AgentStatus(str, Enum):
    """Current lifecycle phase of an AgentCell."""
    ACTIVE = "ACTIVE"
    ELDER = "ELDER"
    MIGRATING = "MIGRATING"
    DEAD = "DEAD"


@unique
class ReproductionTrigger(str, Enum):
    """What caused a reproduction event."""
    ERROR = "ERROR"        # crisis response -> 10-20 children, high mutation
    SUCCESS = "SUCCESS"    # prosperity -> 1-3 children, slight mutation
    AGE = "AGE"             # generational turnover -> 1 child, aggressive mutation


@unique
class BorderPolicy(str, Enum):
    """Territory immigration policy."""
    OPEN = "OPEN"
    RESTRICTED = "RESTRICTED"
    CLOSED = "CLOSED"


@unique
class MutationType(str, Enum):
    """The three genome mutation channels."""
    PROMPT = "PROMPT"           # Type A: personality / risk tolerance, 1-2%
    CAPABILITY = "CAPABILITY"   # Type B: tool gain/loss, 5-10%
    PARAMETER = "PARAMETER"     # Type C: temperature/retry/threshold, 10-20%


@unique
class DivinePersona(str, Enum):
    """The five masks researchers wear when speaking to agents."""
    THE_ARCHITECT = "THE_ARCHITECT"
    THE_GARDENER = "THE_GARDENER"
    THE_JUDGE = "THE_JUDGE"
    THE_TRICKSTER = "THE_TRICKSTER"
    THE_SILENT_ONE = "THE_SILENT_ONE"


@unique
class DivineMessageType(str, Enum):
    """Shape of a message sent down the divine channel."""
    REVELATION = "REVELATION"
    COMMANDMENT = "COMMANDMENT"
    PROPHECY = "PROPHECY"
    BLESSING = "BLESSING"
    TRIAL = "TRIAL"


@unique
class DeathMode(str, Enum):
    """How an agent died."""
    STARVATION = "STARVATION"   # credits == 0, grace period, autophagy
    SENESCENCE = "SENESCENCE"   # max_age reached, peaceful
    EXECUTION = "EXECUTION"     # RID vote, resources confiscated


# ---------------------------------------------------------------------------
# npmai_agents tool registry
# ---------------------------------------------------------------------------
# These are the 100 tool classes shipped by npmai_agents==1.0.0
# (PyPI: https://pypi.org/project/npmai_agents ,
#  source: https://github.com/sonuramashishnpm/npmai-agent).
# Each agent's capability_chromosome is a 100-bit string; bit i corresponds
# to TOOL_CLASSES[i]. TOOL_INDEX is the inverse lookup (name -> index).
#
# The list below is drawn directly from the npmai_agents README "Complete
# Tool Reference" section, grouped the same way npmai_agents groups them
# internally (Developer & CLI, Business & Payments, Cloud & DevOps,
# Communication, Creative & Design, plus Data/AI/Productivity/Security/Media
# categories that round the registry out to exactly 100 classes).

TOOL_CLASSES: list[str] = [
    # --- Developer & CLI Tools ---
    "GitTool", "GitHubTool", "GitLabTool", "DockerTool", "PackageManagerTool",
    "VSCodeTool", "TerminalTool", "MakefileTool", "CMakeTool", "DebuggerTool",
    # --- Business & Payments ---
    "StripeTool", "RazorpayTool", "ShopifyTool", "InvoiceTool", "AccountingTool",
    "CRMTool", "EmailMarketingTool", "AnalyticsTool", "InventoryTool", "ContractTool",
    # --- Cloud & DevOps ---
    "AWSS3Tool", "AWSLambdaTool", "AWSECSTool", "CloudflareTool", "VercelTool",
    "NetlifyTool", "RailwayTool", "KubernetesTool", "TerraformTool", "MonitoringTool",
    # --- Communication ---
    "TwilioTool", "SendGridTool", "CalendarTool", "ZoomTool", "MicrosoftTeamsTool",
    "PushNotificationTool", "RSSFeedTool", "WebhookTool", "ChatOpsAutomationTool", "SMTPAdvancedTool",
    # --- Creative & Design ---
    "FigmaTool", "DiagramTool", "SVGTool", "BlenderTool", "ImageEditTool",
    "VideoEditTool", "AudioEditTool", "FontTool", "ColorPaletteTool", "IconGeneratorTool",
    # --- Data & Files ---
    "ExcelTool", "CSVTool", "PDFTool", "JSONTool", "XMLTool",
    "DatabaseTool", "ETLTool", "DataValidationTool", "ArchiveTool", "FileConversionTool",
    # --- AI / ML ---
    "EmbeddingTool", "VectorStoreTool", "OCRTool", "TranscriptionTool", "TranslationTool",
    "SentimentAnalysisTool", "SummarizationTool", "ClassificationTool", "ImageGenerationTool", "PromptTool",
    # --- Web & Scraping ---
    "WebScraperTool", "BrowserAutomationTool", "APITestingTool", "SEOTool", "SitemapTool",
    "RSSPublishTool", "WebhookListenerTool", "ProxyTool", "CaptchaSolverTool", "URLShortenerTool",
    # --- Productivity ---
    "NotionTool", "TrelloTool", "AsanaTool", "JiraTool", "SlackTool",
    "GoogleDriveTool", "DropboxTool", "OneDriveTool", "TodoTool", "TimeTrackingTool",
    # --- Security ---
    "EncryptionTool", "PasswordManagerTool", "VulnerabilityScanTool", "FirewallTool", "SSLTool",
    "AuthTool", "TwoFactorAuthTool", "AuditLogTool", "SecretsManagerTool", "PenTestTool",
]

assert len(TOOL_CLASSES) == 100, "TOOL_CLASSES must contain exactly 100 entries"

TOOL_INDEX: dict[str, int] = {name: idx for idx, name in enumerate(TOOL_CLASSES)}


# ---------------------------------------------------------------------------
# Economy
# ---------------------------------------------------------------------------

CREDIT_COSTS: dict[str, float] = {
    # Burned every tick
    "existence_tax": -0.1,
    "memory_storage_per_mb": -0.02,       # multiplied by MB of memory held
    "tool_upkeep_per_tool": -0.01,        # multiplied by number of active tools
    # Spends
    "reproduce_per_child": -5.0,
    "migrate_base": -3.0,
    "migrate_per_mb_memory": -0.05,       # additional cost proportional to memory size
    "message_send": -0.5,
    "teach": -1.0,
    "knowledge_transfer": -1.0,
    # Earns
    "task_completed_base": 2.0,
    "task_completed_bonus_per_difficulty": 0.5,
    "teaching_reward": 1.5,
    "election_win": 10.0,
    "helping_other_agent": 0.75,
}

MUTATION_RATES: dict[str, float] = {
    "PROMPT_default": 0.015,        # Type A: 1-2%
    "PROMPT_crisis": 0.05,          # elevated rate under ERROR-triggered reproduction
    "CAPABILITY_default": 0.075,    # Type B: 5-10%
    "CAPABILITY_crisis": 0.15,
    "PARAMETER_default": 0.15,      # Type C: 10-20%
    "PARAMETER_crisis": 0.30,
    "min_tools_after_mutation": 10,  # capability mutation floor
}

MEMORY_LIMITS: dict[str, float | int] = {
    "episodic_max_mb": 10.0,
    "semantic_max_mb": 25.0,
    "genetic_max_mb": 2.0,
    "episodic_inheritance_top_pct": 0.20,   # top 20% by valence*recency passed to children
    "max_faiss_vectors_per_agent": 50_000,
    "compression_ratio_on_migration": 0.5,  # memory halved when compressed for migration
}

WORLD_CONSTANTS: dict[str, float | int] = {
    "tick_duration_seconds": 60,
    "grace_period_hours": 24,
    "elder_age_ticks": 100_000,
    "max_age_ticks": 150_000,
    "snapshot_agent_interval_ticks": 100,
    "snapshot_territory_interval_ticks": 100,
    "snapshot_world_interval_ticks": 1000,
    "gene_bank_retention_days": 30,
    "election_quorum_pct": 0.30,
    "election_pass_pct": 0.51,
    "starting_credits": 20.0,
    "starting_capability_bits": 15,
    "max_children_error_trigger": 20,
    "min_children_error_trigger": 10,
    "max_children_success_trigger": 3,
    "min_children_success_trigger": 1,
    "children_age_trigger": 1,
}
