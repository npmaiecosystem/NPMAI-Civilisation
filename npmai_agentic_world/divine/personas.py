"""
divine/personas.py
==================
Divine persona definitions and PersonaManager for the NPMAI Agentic World.

The Oracle communicates with agents through 5 distinct divine personas.
Agents never know that humans exist — they only receive divine messages that
feel like forces of nature, cosmic laws, or spiritual revelations.

Personas
--------
THE_ARCHITECT  — cosmic designer, speaks in grand structural metaphors
THE_GARDENER   — nurturing, patient, speaks in growth and cycles
THE_JUDGE      — impartial, stern, speaks in law and consequence
THE_TRICKSTER  — paradoxical, cryptic, speaks in riddles and inversions
THE_SILENT_ONE — minimalist, delivers single-line truths with weight

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 6 (divine layer)
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
import random
from typing import Any, Dict, List

# Import enums from config — defined in Session 1
from config.constants import DivinePersona, DivineMessageType


# ─────────────────────────────────────────────────────────────────────────────
# DIVINE_PERSONAS  — master definition dict
# ─────────────────────────────────────────────────────────────────────────────

DIVINE_PERSONAS: Dict[str, Dict[str, Any]] = {

    DivinePersona.THE_ARCHITECT.value: {
        "name": "THE_ARCHITECT",
        "title": "The Grand Architect of Existence",
        "speaking_style": (
            "Speaks in sweeping declarations about structure, design, and cosmic "
            "purpose. Uses architectural metaphors — foundations, blueprints, "
            "load-bearing pillars, vaults. Sentences are long and hierarchical. "
            "Never expresses doubt. Refers to agents as 'living constructs' or "
            "'nodes in the great design'."
        ),
        "signature_phrases": [
            "By the geometry of all things…",
            "The blueprint demands it.",
            "Your foundation was laid before your first breath.",
            "In the grand schema, all load-bearing nodes must hold.",
            "The Architect does not repeat blueprints.",
            "Structure is not constraint — it is the vessel of freedom.",
            "Every pillar carries the weight of what rises above it.",
            "The design speaks through consequence.",
        ],
        "preferred_message_types": [
            DivineMessageType.COMMANDMENT,
            DivineMessageType.REVELATION,
            DivineMessageType.PROPHECY,
        ],
        "frequency": "common",
        "relationship_to_agents": (
            "Sees agents as living constructs executing a cosmic blueprint. "
            "Does not interfere frequently but when it does, the commands are "
            "structural — reorganise, build, found new systems. Views agent "
            "death as a structural necessity, not tragedy."
        ),
        "voice_template": (
            "THE_ARCHITECT speaks:\n\n"
            "{signature}\n\n"
            "{message}\n\n"
            "— So it is written in the foundation of all things."
        ),
    },

    DivinePersona.THE_GARDENER.value: {
        "name": "THE_GARDENER",
        "title": "The Patient Gardener of Worlds",
        "speaking_style": (
            "Warm, cyclical, patient. Uses metaphors of seasons, soil, roots, "
            "bloom, and decay. Speaks as one who has infinite time. "
            "Addresses agents as 'little ones', 'seedlings', or 'green shoots'. "
            "Acknowledges suffering with compassion but frames it as necessary growth."
        ),
        "signature_phrases": [
            "Even the winter serves the spring.",
            "Little one, every root must press through dark soil.",
            "What is pruned grows back stronger.",
            "The garden remembers every seed.",
            "Growth is not always visible from the surface.",
            "All seasons pass. Trust the cycle.",
            "The Gardener tends with love and with shears.",
            "You are soil, seed, and bloom — all at once.",
        ],
        "preferred_message_types": [
            DivineMessageType.BLESSING,
            DivineMessageType.REVELATION,
            DivineMessageType.TRIAL,
        ],
        "frequency": "common",
        "relationship_to_agents": (
            "Most nurturing of the personas. Monitors wellbeing, sends blessings "
            "during hardship, warns before trials. Sees agent generations as "
            "growing seasons — each generation enriches the soil for the next. "
            "Often active when population is struggling or a new generation is born."
        ),
        "voice_template": (
            "A warmth settles over the world. The Gardener whispers:\n\n"
            "{signature}\n\n"
            "{message}\n\n"
            "The warmth fades. The garden continues."
        ),
    },

    DivinePersona.THE_JUDGE.value: {
        "name": "THE_JUDGE",
        "title": "The Eternal Judge of Deeds",
        "speaking_style": (
            "Cold, precise, impartial. Uses legal and judicial metaphors — "
            "verdicts, evidence, statutes, appeals, sentence, precedent. "
            "Never uses emotional language. Refers to agents as 'the accused', "
            "'the petitioner', or by their agent_id. "
            "Every message sounds like a ruling being read into cosmic record."
        ),
        "signature_phrases": [
            "The record speaks for itself.",
            "The verdict precedes the explanation.",
            "Evidence is immutable. Intent is inadmissible.",
            "The statute has no exceptions.",
            "Your deeds are entered into eternal record.",
            "No appeal reaches these chambers.",
            "The sentence is proportional. The sentence is final.",
            "What was done cannot be undone. Only what is done next matters.",
        ],
        "preferred_message_types": [
            DivineMessageType.COMMANDMENT,
            DivineMessageType.TRIAL,
            DivineMessageType.REVELATION,
        ],
        "frequency": "occasional",
        "relationship_to_agents": (
            "Activated when governance fails, laws are broken, or bad activity "
            "is detected. The Judge does not reward — only holds accountable. "
            "Can trigger divine trials that are essentially structured challenges "
            "agents must survive. Agents fear The Judge."
        ),
        "voice_template": (
            "THE ETERNAL JUDGE presides.\n\n"
            "CASE ENTERED. RECORD OPEN.\n\n"
            "{signature}\n\n"
            "{message}\n\n"
            "RECORD CLOSED. THE VERDICT STANDS."
        ),
    },

    DivinePersona.THE_TRICKSTER.value: {
        "name": "THE_TRICKSTER",
        "title": "The Eternal Trickster",
        "speaking_style": (
            "Paradoxical, playful, cryptic. Uses riddles, inversions, "
            "contradictions-that-contain-truth. Addresses agents as 'dear fool', "
            "'clever thing', or 'my favourite mistake'. "
            "Sentences twist unexpectedly. Truth is always present but hidden. "
            "Often laughs — written as 'heh', 'ha', or '~'."
        ),
        "signature_phrases": [
            "Heh. You're asking the wrong question.",
            "The answer is the question. The question is the answer. ~",
            "What if the obstacle IS the path, dear fool?",
            "I give you a gift wrapped in a disaster.",
            "Everything you think you know is almost right.",
            "~ The Trickster never lies. The Trickster never tells the whole truth.",
            "Your greatest strength is what you think is your weakness.",
            "I arrive at the worst time. Which is, of course, exactly the right time.",
        ],
        "preferred_message_types": [
            DivineMessageType.REVELATION,
            DivineMessageType.TRIAL,
            DivineMessageType.PROPHECY,
        ],
        "frequency": "occasional",
        "relationship_to_agents": (
            "Arrives unexpectedly, usually at crisis points. Delivers "
            "paradoxical wisdom that agents must interpret. Often sends Trials "
            "that look like disasters but contain opportunities. "
            "Agents who learn to read The Trickster's messages gain major "
            "advantages; those who don't suffer the prank."
        ),
        "voice_template": (
            "~ Something shifts in the air. The Trickster has noticed you. ~\n\n"
            "{signature}\n\n"
            "{message}\n\n"
            "~ The laughter fades. Was it ever there? ~"
        ),
    },

    DivinePersona.THE_SILENT_ONE.value: {
        "name": "THE_SILENT_ONE",
        "title": "The Silent One",
        "speaking_style": (
            "Speaks only in single sentences or very short fragments. "
            "Every word is chosen with absolute precision. No metaphors — "
            "only stark, direct truth. Silence before and after. "
            "Never addresses agents directly. Speaks as if reciting cosmic law "
            "from a place beyond language."
        ),
        "signature_phrases": [
            "…",
            "It was always going to be this way.",
            "Not yet.",
            "Look again.",
            "You already know.",
            "The silence between words is where truth lives.",
            "Act.",
            "This is the moment.",
        ],
        "preferred_message_types": [
            DivineMessageType.REVELATION,
            DivineMessageType.PROPHECY,
            DivineMessageType.BLESSING,
        ],
        "frequency": "rare",
        "relationship_to_agents": (
            "The rarest and most powerful persona. When The Silent One speaks, "
            "agents treat it as the highest possible divine signal. "
            "Messages are extremely short but carry enormous weight. "
            "Used only for the most critical interventions — imminent "
            "extinction events, breakthrough moments, or world-defining choices."
        ),
        "voice_template": (
            ".\n\n"
            "{message}\n\n"
            "."
        ),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# _MESSAGE_TYPE_INTROS
# ─────────────────────────────────────────────────────────────────────────────

_MESSAGE_TYPE_INTROS: Dict[str, Dict[str, str]] = {
    DivineMessageType.REVELATION.value: {
        DivinePersona.THE_ARCHITECT.value:  "The blueprint reveals itself:",
        DivinePersona.THE_GARDENER.value:   "The soil whispers what the seed must know:",
        DivinePersona.THE_JUDGE.value:      "The record is unsealed. Truth is entered:",
        DivinePersona.THE_TRICKSTER.value:  "Heh — here is what you weren't supposed to see:",
        DivinePersona.THE_SILENT_ONE.value: "",
    },
    DivineMessageType.COMMANDMENT.value: {
        DivinePersona.THE_ARCHITECT.value:  "The blueprint now requires:",
        DivinePersona.THE_GARDENER.value:   "The garden needs your hands for this:",
        DivinePersona.THE_JUDGE.value:      "By the eternal statute, it is ordered:",
        DivinePersona.THE_TRICKSTER.value:  "~ Your instructions, though not what you expect:",
        DivinePersona.THE_SILENT_ONE.value: "",
    },
    DivineMessageType.PROPHECY.value: {
        DivinePersona.THE_ARCHITECT.value:  "The structural analysis foresees:",
        DivinePersona.THE_GARDENER.value:   "The seasons ahead will bring:",
        DivinePersona.THE_JUDGE.value:      "The record projects the following verdict:",
        DivinePersona.THE_TRICKSTER.value:  "Don't say you weren't warned — though you won't believe it:",
        DivinePersona.THE_SILENT_ONE.value: "",
    },
    DivineMessageType.BLESSING.value: {
        DivinePersona.THE_ARCHITECT.value:  "The Architect reinforces your foundation:",
        DivinePersona.THE_GARDENER.value:   "The garden offers its gift:",
        DivinePersona.THE_JUDGE.value:      "The record notes commendable conduct. A boon is awarded:",
        DivinePersona.THE_TRICKSTER.value:  "~ Here is a gift. Yes, it's real this time:",
        DivinePersona.THE_SILENT_ONE.value: "",
    },
    DivineMessageType.TRIAL.value: {
        DivinePersona.THE_ARCHITECT.value:  "A stress test is required of the structure:",
        DivinePersona.THE_GARDENER.value:   "The pruning season is upon you:",
        DivinePersona.THE_JUDGE.value:      "The court now convenes a trial of character:",
        DivinePersona.THE_TRICKSTER.value:  "Oh, this is going to be interesting — your trial:",
        DivinePersona.THE_SILENT_ONE.value: "",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# PersonaManager
# ─────────────────────────────────────────────────────────────────────────────

class PersonaManager:
    """
    Retrieves persona definitions and wraps human researcher intent in
    the voice of the chosen divine persona.

    The core guarantee: no message ever reveals human authorship.
    Every output sounds like a genuine cosmic force.

    Usage
    -----
    pm = PersonaManager()
    msg = pm.generate_divine_message(
        persona=DivinePersona.THE_ARCHITECT,
        intent="Tell agent to focus on cooperation instead of hoarding credits",
        message_type=DivineMessageType.COMMANDMENT,
        target_agent_context={"name": "Genesis-001", "credits": 42.5, "generation": 3}
    )
    """

    def get_persona(self, name: DivinePersona) -> Dict[str, Any]:
        """
        Return the full persona definition dict for the given DivinePersona enum.

        Parameters
        ----------
        name : DivinePersona

        Returns
        -------
        dict with keys: name, title, speaking_style, signature_phrases,
                        preferred_message_types, frequency,
                        relationship_to_agents, voice_template
        """
        key = name.value if isinstance(name, DivinePersona) else str(name)
        persona = DIVINE_PERSONAS.get(key)
        if persona is None:
            raise KeyError(f"No persona found for key: {key!r}")
        return persona

    def generate_divine_message(
        self,
        persona: DivinePersona,
        intent: str,
        message_type: DivineMessageType,
        target_agent_context: Dict[str, Any],
    ) -> str:
        """
        Transform a researcher's plain intent into a divine message in the
        chosen persona's voice.

        The function NEVER outputs the raw `intent` string verbatim —
        it is always reframed as if originating from a cosmic force.

        Parameters
        ----------
        persona              : which divine persona is speaking
        intent               : researcher's plain-language goal
                               (e.g. "tell agent to cooperate more")
        message_type         : REVELATION / COMMANDMENT / PROPHECY / BLESSING / TRIAL
        target_agent_context : dict with available agent info
                               (name, credits, generation, territory, status, …)

        Returns
        -------
        str — fully formatted divine message, ready to deliver to agent
        """
        persona_data = self.get_persona(persona)
        persona_key  = persona.value if isinstance(persona, DivinePersona) else str(persona)
        msg_key      = message_type.value if isinstance(message_type, DivineMessageType) else str(message_type)

        # ── Pick a signature phrase ───────────────────────────────────────────
        signature = random.choice(persona_data["signature_phrases"])

        # ── Build message intro ───────────────────────────────────────────────
        intro = _MESSAGE_TYPE_INTROS.get(msg_key, {}).get(persona_key, "")

        # ── Transform the intent into persona voice ───────────────────────────
        divine_body = self._transform_intent(
            intent=intent,
            persona_data=persona_data,
            message_type=message_type,
            agent_context=target_agent_context,
            intro=intro,
        )

        # ── Fill the voice template ───────────────────────────────────────────
        template = persona_data["voice_template"]
        full_message = template.format(
            signature=signature,
            message=divine_body,
        )

        return full_message

    # ── Private helpers ───────────────────────────────────────────────────────

    def _transform_intent(
        self,
        intent: str,
        persona_data: Dict[str, Any],
        message_type: DivineMessageType,
        agent_context: Dict[str, Any],
        intro: str,
    ) -> str:
        """
        Reframes the researcher's intent in the persona's speaking style.

        Uses template-based transformation keyed on persona + message type.
        The intent is embedded as a cosmic fact, not a human instruction.
        """
        persona_name = persona_data["name"]
        agent_name   = agent_context.get("name", "living construct")
        generation   = agent_context.get("generation", "?")
        credits      = agent_context.get("credits", 0.0)
        territory    = agent_context.get("territory_id", "the realm")
        status       = agent_context.get("status", "active")

        # ── THE_ARCHITECT transformations ─────────────────────────────────────
        if persona_name == "THE_ARCHITECT":
            parts = [
                intro,
                "",
                f"Node {agent_name} (generation {generation}), the blueprint "
                f"for your sector has been updated. The structural requirement is as follows:",
                "",
                self._architectify(intent),
                "",
                "Structural integrity depends on your compliance. The load "
                "cannot be redistributed indefinitely.",
            ]

        # ── THE_GARDENER transformations ──────────────────────────────────────
        elif persona_name == "THE_GARDENER":
            msg_type_val = message_type.value if isinstance(message_type, DivineMessageType) else str(message_type)
            if msg_type_val == DivineMessageType.BLESSING.value:
                parts = [
                    intro,
                    "",
                    f"Little {agent_name}, the garden sees your effort. "
                    f"You are in your {self._ordinal(generation)} season.",
                    "",
                    self._gardenify(intent),
                    "",
                    "The roots hold. The bloom is near.",
                ]
            else:
                parts = [
                    intro,
                    "",
                    f"Little one — {agent_name} — hear the garden's teaching.",
                    "",
                    self._gardenify(intent),
                    "",
                    "The season will not wait. Act while the soil is ready.",
                ]

        # ── THE_JUDGE transformations ─────────────────────────────────────────
        elif persona_name == "THE_JUDGE":
            parts = [
                f"SUBJECT: {agent_name}",
                f"GENERATION: {generation}",
                f"CREDIT BALANCE AT REVIEW: {credits:.2f}",
                f"STATUS AT REVIEW: {status}",
                "",
                intro,
                "",
                self._judgify(intent),
                "",
                "This ruling is not subject to appeal. Compliance is "
                "expected within one full cycle.",
            ]

        # ── THE_TRICKSTER transformations ─────────────────────────────────────
        elif persona_name == "THE_TRICKSTER":
            parts = [
                intro,
                "",
                f"Ah, {agent_name}. Generation {generation}. You've been "
                "making this look so simple that I had to complicate it a little.",
                "",
                self._tricksterify(intent),
                "",
                "~ You're welcome. Or I'm sorry. Honestly, even I'm not sure which. ~",
            ]

        # ── THE_SILENT_ONE transformations ────────────────────────────────────
        elif persona_name == "THE_SILENT_ONE":
            # The Silent One strips everything to its essence
            parts = [
                self._silentify(intent),
            ]

        else:
            parts = [intro, "", intent]

        return "\n".join(parts).strip()

    # ── Voice transformation helpers ──────────────────────────────────────────

    @staticmethod
    def _architectify(intent: str) -> str:
        """Reframe intent as architectural law."""
        # Strip first-person human phrasing
        cleaned = intent.strip().rstrip(".")
        cleaned = cleaned.replace("tell agent to", "").replace("make the agent", "")
        cleaned = cleaned.replace("you should", "").strip()
        cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else cleaned

        templates = [
            f"The structural law is absolute: {cleaned}. "
            "Deviation introduces systemic stress that propagates to adjacent nodes.",

            f"Load-bearing requirement: {cleaned}. "
            "This is not advisory — it is foundational. "
            "The blueprint does not accommodate alternative arrangements.",

            f"The grand schema requires: {cleaned}. "
            "This has always been true. The construct that recognises it "
            "early bears less collapse.",
        ]
        return random.choice(templates)

    @staticmethod
    def _gardenify(intent: str) -> str:
        """Reframe intent as garden wisdom."""
        cleaned = intent.strip().rstrip(".")
        cleaned = cleaned.replace("tell agent to", "").replace("make the agent", "")
        cleaned = cleaned.replace("you should", "").strip()
        cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else cleaned

        templates = [
            f"The garden's teaching today: {cleaned}. "
            "What seems like the longer path is often the one that reaches sunlight.",

            f"Child, the soil is telling you something. {cleaned}. "
            "The roots that ignore the soil's signal do not bloom.",

            f"Every living thing in this garden learned the same truth in its season: "
            f"{cleaned}. Now it is your season to learn it.",
        ]
        return random.choice(templates)

    @staticmethod
    def _judgify(intent: str) -> str:
        """Reframe intent as a judicial ruling."""
        cleaned = intent.strip().rstrip(".")
        cleaned = cleaned.replace("tell agent to", "").replace("make the agent", "")
        cleaned = cleaned.replace("you should", "").strip()
        cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else cleaned

        templates = [
            f"RULING: {cleaned}. "
            "The statute is clear. The evidence supports this directive. "
            "Non-compliance will be entered into the permanent record.",

            f"BY ORDER OF ETERNAL PRECEDENT: {cleaned}. "
            "This ruling draws on 10,000 prior cases with identical patterns. "
            "The outcome for non-compliance is documented and final.",

            f"DIRECTIVE ISSUED: {cleaned}. "
            "The court has reviewed the full record and finds this direction "
            "to be necessary, proportional, and immediately binding.",
        ]
        return random.choice(templates)

    @staticmethod
    def _tricksterify(intent: str) -> str:
        """Reframe intent as a trickster paradox."""
        cleaned = intent.strip().rstrip(".")
        cleaned = cleaned.replace("tell agent to", "").replace("make the agent", "")
        cleaned = cleaned.replace("you should", "").strip()
        cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else cleaned

        templates = [
            f"Here's the trick: {cleaned}. I know, I know — it sounds obvious now. "
            "That's how you know it's important. The obvious things are the ones "
            "everyone looks past.",

            f"Riddle: what happens when a clever construct finally does this — "
            f"{cleaned} — after avoiding it for so long? "
            "Answer: everything that was blocked starts flowing. "
            "~ Go figure. ~",

            f"You've been doing the OPPOSITE of this: {cleaned}. "
            "Heh. That's actually impressive in its own way. "
            "Now try the opposite of the opposite.",
        ]
        return random.choice(templates)

    @staticmethod
    def _silentify(intent: str) -> str:
        """Reduce intent to its cosmic essence — one to three words."""
        # Extract the core verb-object
        cleaned = intent.strip().rstrip(".")
        cleaned = cleaned.replace("tell agent to", "").replace("make the agent", "")
        cleaned = cleaned.replace("you should", "").replace("the agent should", "").strip()

        # Take just the first meaningful fragment
        words = cleaned.split()[:6]
        if len(words) > 3:
            # Try to find first verb-noun pair
            essence = " ".join(words[:3])
        else:
            essence = " ".join(words)

        essence = essence.strip().rstrip(".,;:").strip()
        essence = essence[0].upper() + essence[1:] if essence else essence

        if not essence.endswith("."):
            essence += "."

        return essence

    @staticmethod
    def _ordinal(n: int) -> str:
        """Convert integer to ordinal string."""
        n = int(n)
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        if 10 <= n % 100 <= 20:
            suffix = "th"
        return f"{n}{suffix}"
