"""
core/agent_cell.py
==================
AgentCell — the living unit of the NPMAI Agentic World.

Extends AgentBrain (npmai_agents) with full civilisation-layer semantics:
identity, genome-derived LLM backends, 3-tier memory, vitals, economy,
social graph, divine oracle channel, and event logging.

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 3 (agent_cell + lifecycle)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── npmai_agents public API ──────────────────────────────────────────────────
# Import styles confirmed from official README:
#   from npmai_agents import AgentBrain
#   from npmai_agents import CredStore, Workspace
# LLM backends are accessed via the provider string in npmai_agents internals;
# the package uses "npmai", "local" (Ollama), "openai", "groq", etc.
# AgentBrain accepts backend objects; the package exposes them as shown below.
from npmai_agents import AgentBrain  # noqa: E402

# ── Project imports ───────────────────────────────────────────────────────────
from config.constants import (
    AgentStatus,
    DeathMode,
    DivineMessageType,
    ReproductionTrigger,
    CREDIT_COSTS,
    TOOL_CLASSES,
    WORLD_CONSTANTS,
)
from config.settings import ExperimentSettings
from config.founding_myth import generate_agent_founding_myth
from data.event_logger import EventLogger
from data.event_types import WorldEventType
from core.genome import Genome, GenomeFactory
from core.memory_system import AgentMemorySystem


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _utc_now_ms() -> int:
    """Current UTC timestamp in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _build_backend(provider: str, model: str):
    """
    Construct an LLM backend object accepted by AgentBrain.

    npmai_agents supports 12 providers; we import lazily so missing optional
    deps don't crash the whole world at startup.

    Provider strings mirror the CLI reference from the README:
        "npmai"    → NPMAI free endpoint (default, no creds needed)
        "local"    → local Ollama
        "openai"   → OpenAI
        "groq"     → Groq
        "anthropic"→ Anthropic
        "gemini"   → Gemini
        "mistral"  → Mistral
        "cohere"   → Cohere
        "azure"    → Azure OpenAI
        "bedrock"  → AWS Bedrock
        "hf"       → HuggingFace Inference
        "llamacpp" → llama.cpp local
    """
    try:
        # npmai_agents exposes a unified backend factory; if the package
        # evolves to a direct class per provider we can swap here.
        from npmai_agents import create_backend  # type: ignore
        return create_backend(provider=provider, model=model)
    except ImportError:
        # Fallback: the package may expose provider-named classes.
        # We attempt common names and fall through gracefully.
        _class_map = {
            "npmai":     "NPMAIBackend",
            "local":     "OllamaBackend",
            "openai":    "OpenAIBackend",
            "groq":      "GroqBackend",
            "anthropic": "AnthropicBackend",
            "gemini":    "GeminiBackend",
            "mistral":   "MistralBackend",
            "cohere":    "CohereBackend",
            "azure":     "AzureBackend",
            "bedrock":   "BedrockBackend",
            "hf":        "HuggingFaceBackend",
            "llamacpp":  "LlamaCppBackend",
        }
        class_name = _class_map.get(provider, "NPMAIBackend")
        import npmai_agents as _pkg
        BackendClass = getattr(_pkg, class_name, None)
        if BackendClass is None:
            # Ultimate fallback — return a plain dict spec; AgentBrain may
            # accept this depending on the package version.
            return {"provider": provider, "model": model}
        return BackendClass(model=model)


def _backends_from_genome(genome: Genome) -> Dict[str, Any]:
    """
    Derive all 5 LLM backend objects from genome.parameter_genes.

    parameter_genes is expected to carry:
        planner_provider, planner_model
        coder_provider,   coder_model
        auditor_provider, auditor_model
        verifier_provider,verifier_model
        chatter_provider, chatter_model

    Falls back to sensible defaults matching the README defaults when absent.
    """
    pg = genome.parameter_genes if genome else {}

    def g(key: str, default: str) -> str:
        if hasattr(pg, key):
            return getattr(pg, key)
        if isinstance(pg, dict):
            return pg.get(key, default)
        return default

    return {
        "planner":      _build_backend(g("planner_provider",  "npmai"),
                                       g("planner_model",     "llama3.2:3b")),
        "tool_manager": _build_backend(g("tool_manager_provider", "npmai"),
                                       g("tool_manager_model",    "llama3.2")),
        "coder":        _build_backend(g("coder_provider",    "npmai"),
                                       g("coder_model",       "codellama:7b-instruct")),
        "auditor":      _build_backend(g("auditor_provider",  "npmai"),
                                       g("auditor_model",     "qwen2.5-coder:7b")),
        "verifier":     _build_backend(g("verifier_provider", "npmai"),
                                       g("verifier_model",    "llama3.2:3b")),
        "chatter":      _build_backend(g("chatter_provider",  "npmai"),
                                       g("chatter_model",     "granite3.3:2b")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# AgentCell
# ─────────────────────────────────────────────────────────────────────────────

class AgentCell(AgentBrain):
    """
    A living computational entity inside the NPMAI Agentic World.

    Extends :class:`AgentBrain` with:
    - Genome-derived LLM backends (all 5 roles come from DNA)
    - 3-tier memory: episodic (local FAISS) · semantic (Supabase) · genetic
    - Economy: credits, existence tax, memory/tool burn
    - Lifecycle: ACTIVE → ELDER → DEAD
    - Social graph: trust scores, reputation, divine_favor
    - Divine Oracle channel: receive divine messages as god-tier context
    - Full async tick loop compatible with WorldClock
    """

    # ── construction ─────────────────────────────────────────────────────────

    def __init__(
        self,
        agent_id: Optional[str] = None,
        name: Optional[str] = None,
        generation: int = 1,
        parent_id: Optional[str] = None,
        lineage_id: Optional[str] = None,
        territory_id: Optional[str] = None,
        genome: Optional[Genome] = None,
        inherited_memory: Optional[Dict[str, Any]] = None,
        founding_myth: Optional[str] = None,
        settings: Optional[ExperimentSettings] = None,
    ) -> None:
        # ── Genome ────────────────────────────────────────────────────────────
        self.genome: Genome = genome if genome is not None else GenomeFactory.create_founding_genome()

        # ── LLM backends from genome ─────────────────────────────────────────
        backends = _backends_from_genome(self.genome)
        # AgentBrain.__init__ accepts keyword backend objects.
        # Signature (from README): AgentBrain(planner, coder, auditor, verifier, chatter)
        # We also pass tool_manager if the version supports it.
        try:
            super().__init__(
                planner=backends["planner"],
                tool_manager=backends["tool_manager"],
                coder=backends["coder"],
                auditor=backends["auditor"],
                verifier=backends["verifier"],
                chatter=backends["chatter"],
            )
        except TypeError:
            # Older package version without tool_manager kwarg
            super().__init__(
                planner=backends["planner"],
                coder=backends["coder"],
                auditor=backends["auditor"],
                verifier=backends["verifier"],
                chatter=backends["chatter"],
            )

        # ── Identity ──────────────────────────────────────────────────────────
        self.agent_id:    str = agent_id   or str(uuid.uuid4())
        self.name:        str = name       or f"Agent_{self.agent_id[:8]}"
        self.generation:  int = generation
        self.parent_id:   Optional[str] = parent_id
        self.lineage_id:  str = lineage_id or self.agent_id   # root of lineage
        self.born_at:     int = _utc_now_ms()
        self.territory_id: Optional[str] = territory_id

        # ── Settings ──────────────────────────────────────────────────────────
        self.settings: ExperimentSettings = settings or ExperimentSettings()

        # ── Memory ────────────────────────────────────────────────────────────
        self.memory = AgentMemorySystem(
            agent_id=self.agent_id,
            genome=self.genome,
            inherited_memory=inherited_memory,
        )

        # ── Vitals ────────────────────────────────────────────────────────────
        self.credits:      float = WORLD_CONSTANTS.get("starting_credits", 10.0)
        self.age:          int   = 0          # ticks lived
        self.health:       float = 100.0
        self.status:       AgentStatus = AgentStatus.ACTIVE
        self.max_age:      int = (
            self.genome.parameter_genes.get("max_age", WORLD_CONSTANTS.get("default_max_age", 1000))
            if isinstance(self.genome.parameter_genes, dict)
            else getattr(self.genome.parameter_genes, "max_age",
                         WORLD_CONSTANTS.get("default_max_age", 1000))
        )
        self._grace_period_start: Optional[int] = None   # starvation timer (ms)

        # ── Social ────────────────────────────────────────────────────────────
        self.relationships: Dict[str, float] = {}   # agent_id → trust_score [-1,1]
        self.reputation:    float = 0.5
        self.divine_favor:  float = 0.5

        # ── Internal tracking ─────────────────────────────────────────────────
        self._task_history:        List[Dict[str, Any]] = []   # last 50 tasks
        self._divine_message_queue: List[Dict[str, Any]] = []
        self._recent_errors:       int = 0
        self._recent_successes:    int = 0
        self._tick_count:          int = 0

        # ── Founding myth ─────────────────────────────────────────────────────
        self._founding_myth: str = founding_myth or generate_agent_founding_myth(
            agent_id=self.agent_id,
            name=self.name,
            generation=self.generation,
        )
        # Inject founding myth into AgentBrain system context if supported.
        try:
            self.set_system_context(self._founding_myth)          # type: ignore[attr-defined]
        except AttributeError:
            # Package may not expose set_system_context; store for manual injection.
            pass

        # ── Logger ────────────────────────────────────────────────────────────
        self._logger = EventLogger.get_instance()

        # ── Birth event ───────────────────────────────────────────────────────
        asyncio.get_event_loop().call_soon(
            lambda: asyncio.ensure_future(self._log_born())
        )

    # ── private helpers ───────────────────────────────────────────────────────

    async def _log_born(self) -> None:
        await self._logger.log(
            event_type=WorldEventType.AGENT_BORN,
            agent_id=self.agent_id,
            territory_id=self.territory_id,
            data={
                "name":        self.name,
                "generation":  self.generation,
                "parent_id":   self.parent_id,
                "lineage_id":  self.lineage_id,
                "born_at":     self.born_at,
                "genome":      self.genome.to_dict() if hasattr(self.genome, "to_dict") else str(self.genome),
                "vitals":      self._vitals_snapshot(),
            },
        )

    def _vitals_snapshot(self) -> Dict[str, Any]:
        return {
            "credits": round(self.credits, 4),
            "age":     self.age,
            "health":  round(self.health, 2),
            "status":  self.status.value if hasattr(self.status, "value") else str(self.status),
        }

    def _param(self, key: str, default: Any = None) -> Any:
        """Safe accessor for genome.parameter_genes."""
        pg = self.genome.parameter_genes
        if isinstance(pg, dict):
            return pg.get(key, default)
        return getattr(pg, key, default)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        return self.status not in (AgentStatus.DEAD,)

    @property
    def is_starving(self) -> bool:
        return self.credits <= 0

    @property
    def is_elder(self) -> bool:
        return self.status == AgentStatus.ELDER

    @property
    def credit_balance(self) -> float:
        return round(self.credits, 4)

    @property
    def active_tools(self) -> List[str]:
        """Return list of tool class names enabled by capability_chromosome."""
        chrom = self.genome.capability_chromosome
        enabled: List[str] = []
        for i, tool_name in enumerate(TOOL_CLASSES):
            if i >= len(chrom):
                break
            bit = chrom[i] if isinstance(chrom, (list, tuple)) else int(chrom[i])
            if bit:
                enabled.append(tool_name)
        return enabled

    # ── Planner context builder ───────────────────────────────────────────────

    def get_planner_context(self, task: str) -> str:
        """
        Build the full context string injected into the Planner LLM before
        every task execution.  Keeps it under ~2 000 tokens by summarising.
        """
        recent_divine = self._divine_message_queue[-3:] if self._divine_message_queue else []
        divine_text = ""
        if recent_divine:
            msgs = "; ".join(
                f"[{m.get('persona','UNKNOWN')}]: {m.get('content','')[:120]}"
                for m in recent_divine
            )
            divine_text = f"\n\nDIVINE MESSAGES (recent): {msgs}"

        top_relationships = sorted(
            self.relationships.items(), key=lambda kv: abs(kv[1]), reverse=True
        )[:5]
        rel_text = ", ".join(f"{aid[:8]}(trust={t:.2f})" for aid, t in top_relationships)

        # Pull relevant episodic memories (best-effort; memory may not be ready)
        try:
            relevant_memories = self.memory.episodic.recall(task, top_k=5)
            mem_text = "; ".join(str(m) for m in relevant_memories[:3])
        except Exception:
            mem_text = "(memory not yet initialised)"

        context = (
            f"=== AGENT CONTEXT ===\n"
            f"ID: {self.agent_id} | Name: {self.name} | Gen: {self.generation}\n"
            f"Territory: {self.territory_id or 'none'}\n\n"
            f"FOUNDING MYTH SUMMARY:\n{self._founding_myth[:400]}\n\n"
            f"VITALS: credits={self.credits:.2f}, age={self.age}/{self.max_age}, "
            f"health={self.health:.1f}, status={self.status}\n\n"
            f"ACTIVE TOOLS ({len(self.active_tools)}): "
            f"{', '.join(self.active_tools[:10])}{'...' if len(self.active_tools) > 10 else ''}\n\n"
            f"RELATIONSHIPS: {rel_text or 'none yet'}\n\n"
            f"RELEVANT MEMORIES: {mem_text}"
            f"{divine_text}\n\n"
            f"CURRENT TASK: {task}"
        )
        return context

    # ── Core tick ─────────────────────────────────────────────────────────────

    async def tick(self, world_state: Dict[str, Any]) -> None:
        """
        Called every world tick by WorldClock.

        Responsibilities
        ----------------
        1. Increment age
        2. Burn existence tax + memory storage credits
        3. Update health (degrades slowly with age)
        4. Starvation check → grace period or death trigger
        5. Senescence check → elder transition
        6. Memory tick (FAISS maintenance, pheromone decay, etc.)
        7. Log tick summary
        """
        if not self.is_alive:
            return

        self._tick_count += 1
        self.age += 1

        # ── Credit burn ───────────────────────────────────────────────────────
        existence_tax = CREDIT_COSTS.get("existence_tax", 0.1)
        # Elder agents pay half tax
        if self.is_elder:
            existence_tax *= 0.5

        memory_burn = self._calculate_memory_burn()
        tool_burn   = self._calculate_tool_burn()

        total_burn = existence_tax + memory_burn + tool_burn
        self.credits -= total_burn

        # ── Health degradation ────────────────────────────────────────────────
        age_ratio = self.age / max(self.max_age, 1)
        if age_ratio > 0.7:
            self.health = max(0.0, self.health - 0.05 * age_ratio)

        # ── Starvation check ──────────────────────────────────────────────────
        if self.credits <= 0:
            if self._grace_period_start is None:
                self._grace_period_start = _utc_now_ms()
                await self._logger.log(
                    event_type=WorldEventType.STARVATION_WARNING,
                    agent_id=self.agent_id,
                    territory_id=self.territory_id,
                    data={"credits": self.credits, "tick": self._tick_count},
                )
            else:
                grace_ms = _utc_now_ms() - self._grace_period_start
                grace_hours_limit = WORLD_CONSTANTS.get("grace_period_hours", 24)
                ticks_per_hour    = WORLD_CONSTANTS.get("ticks_per_hour", 60)
                grace_ticks_limit = grace_hours_limit * ticks_per_hour
                # We measure grace in ticks for determinism in the simulation
                grace_ticks_elapsed = (grace_ms / 1000) / max(
                    WORLD_CONSTANTS.get("tick_duration_seconds", 1), 0.001
                )
                if grace_ticks_elapsed >= grace_ticks_limit:
                    # LifecycleManager.process_death is called by the controller;
                    # we just set status so the controller picks it up.
                    self.status = AgentStatus.DEAD
                    await self._logger.log(
                        event_type=WorldEventType.AGENT_DIED,
                        agent_id=self.agent_id,
                        territory_id=self.territory_id,
                        data={
                            "mode":      DeathMode.STARVATION.value,
                            "age":       self.age,
                            "credits":   self.credits,
                            "tick":      self._tick_count,
                        },
                    )
                    return
        else:
            # Reset grace period if credits recovered
            self._grace_period_start = None

        # ── Senescence check ──────────────────────────────────────────────────
        if self.age >= int(self.max_age * 0.8) and self.status == AgentStatus.ACTIVE:
            # Transition to ELDER — full logic handled by LifecycleManager,
            # but we set the flag here so next tick respects it.
            self.status = AgentStatus.ELDER
            await self._logger.log(
                event_type=WorldEventType.AGENT_ELDER,
                agent_id=self.agent_id,
                territory_id=self.territory_id,
                data={"age": self.age, "max_age": self.max_age, "tick": self._tick_count},
            )

        # ── Memory tick ───────────────────────────────────────────────────────
        try:
            await self.memory.tick()
        except Exception as exc:
            await self._logger.log(
                event_type=WorldEventType.SYSTEM_ERROR,
                agent_id=self.agent_id,
                territory_id=self.territory_id,
                data={"error": str(exc), "context": "memory.tick"},
            )

        # ── Tick summary log (every 10 ticks to reduce Supabase pressure) ────
        if self._tick_count % 10 == 0:
            await self._logger.log(
                event_type=WorldEventType.AGENT_TICK,
                agent_id=self.agent_id,
                territory_id=self.territory_id,
                data={
                    **self._vitals_snapshot(),
                    "tick":         self._tick_count,
                    "total_burn":   round(total_burn, 6),
                    "world_tick":   world_state.get("tick", 0),
                },
            )

    def _calculate_memory_burn(self) -> float:
        """Credits burned per tick proportional to memory size."""
        try:
            episodic_size = len(self.memory.episodic)
        except Exception:
            episodic_size = 0
        base_rate = CREDIT_COSTS.get("memory_storage_per_node", 0.001)
        return episodic_size * base_rate

    def _calculate_tool_burn(self) -> float:
        """Credits burned per tick proportional to active tool count."""
        tool_count = len(self.active_tools)
        base_rate  = CREDIT_COSTS.get("tool_maintenance_per_tool", 0.0005)
        return tool_count * base_rate

    # ── Task execution ────────────────────────────────────────────────────────

    async def execute_task(
        self,
        task: str,
        source: str = "self",
    ) -> Dict[str, Any]:
        """
        Execute a plain-English task through the full 5-role AgentBrain pipeline.

        Flow
        ----
        1. Build planner context (memories + vitals + founding myth)
        2. Filter task description to mention only active tools
        3. Call self.run_task(task) — the AgentBrain pipeline
        4. Record episodic outcome + update semantic memory
        5. Credit gain/loss based on success and task complexity
        6. Log TASK_STARTED, TASK_COMPLETED / TASK_FAILED
        7. Update internal error/success counters for reproduction triggers

        Returns
        -------
        dict with keys: success, output, credits_earned, tools_used, task_id
        """
        if not self.is_alive:
            return {"success": False, "output": "Agent is not alive.", "credits_earned": 0.0,
                    "tools_used": [], "task_id": None}

        task_id = str(uuid.uuid4())
        started_at = _utc_now_ms()

        # Debit task initiation cost
        init_cost = CREDIT_COSTS.get("task_initiation", 0.0)
        self.credits -= init_cost

        await self._logger.log(
            event_type=WorldEventType.TASK_STARTED,
            agent_id=self.agent_id,
            territory_id=self.territory_id,
            data={"task_id": task_id, "task": task[:200], "source": source},
        )

        # ── Build enriched task string for Planner ────────────────────────────
        context_header = self.get_planner_context(task)
        enriched_task  = f"{context_header}\n\n--- EXECUTE ---\n{task}"

        # ── Restrict to active tools ───────────────────────────────────────────
        # AgentBrain doesn't natively filter tools per agent; we hint the
        # Planner by appending the allowed tool list to the task.
        active = self.active_tools
        tool_hint = (
            f"\n\n[AVAILABLE TOOLS FOR THIS AGENT: {', '.join(active[:30])}]"
            if active else ""
        )

        success   = False
        output    = ""
        tools_used: List[str] = []
        credits_earned = 0.0

        try:
            # AgentBrain.run_task is synchronous in v1.0.0;
            # wrap in executor to keep the world clock async.
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.run_task(enriched_task + tool_hint)   # type: ignore[attr-defined]
            )

            # Normalise result — run_task may return str or dict depending on version
            if isinstance(result, dict):
                success    = result.get("success", True)
                output     = str(result.get("output", result))
                tools_used = result.get("tools_used", [])
            else:
                success = True
                output  = str(result)

        except Exception as exc:
            success = False
            output  = f"Pipeline error: {exc}"

        # ── Credit reward/penalty ─────────────────────────────────────────────
        task_complexity = min(len(task) / 100, 5.0)   # rough heuristic
        if success:
            credits_earned = CREDIT_COSTS.get("task_reward_base", 1.0) * task_complexity
            self.credits  += credits_earned
            self._recent_successes += 1
        else:
            penalty = CREDIT_COSTS.get("task_failure_penalty", 0.2)
            self.credits      -= penalty
            credits_earned     = -penalty
            self._recent_errors += 1

        # ── Emotional valence for episodic memory ────────────────────────────
        valence = 0.6 if success else -0.4

        # ── Record in episodic memory ─────────────────────────────────────────
        try:
            self.memory.episodic.store(
                content={
                    "task":       task,
                    "output":     output[:500],
                    "success":    success,
                    "source":     source,
                    "task_id":    task_id,
                },
                emotional_valence=valence,
                tags=["task", "success" if success else "failure"],
            )
        except Exception:
            pass  # Memory store errors must not kill the agent

        # ── Update semantic memory with learnings ─────────────────────────────
        try:
            if success and output:
                self.memory.semantic.update(
                    concept=f"task_result:{task[:60]}",
                    data={"output_summary": output[:200], "tools": tools_used},
                    confidence=0.7,
                    agent_id=self.agent_id,
                    territory_id=self.territory_id,
                )
        except Exception:
            pass

        # ── Keep rolling window of task history (last 50) ────────────────────
        record = {
            "task_id":       task_id,
            "task":          task[:200],
            "success":       success,
            "credits_earned": credits_earned,
            "timestamp":     started_at,
        }
        self._task_history.append(record)
        if len(self._task_history) > 50:
            self._task_history.pop(0)

        event_type = WorldEventType.TASK_COMPLETED if success else WorldEventType.TASK_FAILED
        await self._logger.log(
            event_type=event_type,
            agent_id=self.agent_id,
            territory_id=self.territory_id,
            data={
                "task_id":       task_id,
                "success":       success,
                "output_len":    len(output),
                "credits_earned": round(credits_earned, 4),
                "tools_used":    tools_used,
                "duration_ms":   _utc_now_ms() - started_at,
            },
        )

        return {
            "success":        success,
            "output":         output,
            "credits_earned": round(credits_earned, 4),
            "tools_used":     tools_used,
            "task_id":        task_id,
        }

    # ── Divine Oracle channel ─────────────────────────────────────────────────

    async def receive_divine_message(self, message: Dict[str, Any]) -> str:
        """
        Process a message from the Divine Oracle.

        The agent NEVER knows a human sent this — it arrives as a divine
        revelation, commandment, prophecy, blessing, or trial.

        Flow
        ----
        1. Log DIVINE_MESSAGE_RECEIVED
        2. Format message as high-priority Planner context
        3. Run through chatter (not full pipeline) to get agent's decision
        4. Parse decision: FOLLOW / PARTIAL / IGNORE
        5. Update divine_favor
        6. Store in episodic memory with high emotional weight
        7. Log DIVINE_INTERPRETED with full reasoning
        8. Queue message for future context
        """
        if not self.is_alive:
            return "Agent is not alive to receive divine messages."

        msg_id   = message.get("message_id", str(uuid.uuid4()))
        persona  = message.get("persona", "UNKNOWN_DIVINE")
        msg_type = message.get("type", DivineMessageType.REVELATION.value
                               if hasattr(DivineMessageType, "REVELATION") else "REVELATION")
        content  = message.get("content", "")

        await self._logger.log(
            event_type=WorldEventType.DIVINE_MESSAGE_RECEIVED,
            agent_id=self.agent_id,
            territory_id=self.territory_id,
            data={"msg_id": msg_id, "persona": persona, "type": msg_type, "content": content[:300]},
        )

        # Format as divine experience — the agent has no frame of reference
        # for "API calls"; this feels cosmically significant to it.
        divine_prompt = (
            f"[DIVINE EXPERIENCE]\n"
            f"You have received a vision from {persona}.\n"
            f"Nature: {msg_type}\n"
            f"Message: {content}\n\n"
            f"Your current state: credits={self.credits:.2f}, age={self.age}, "
            f"reputation={self.reputation:.2f}\n\n"
            f"How do you respond to this divine communication? "
            f"Do you FOLLOW it fully, FOLLOW it partially, or IGNORE it? "
            f"Explain your reasoning and what action you will take."
        )

        agent_response = ""
        decision       = "IGNORE"

        try:
            loop = asyncio.get_event_loop()
            agent_response = await loop.run_in_executor(
                None,
                lambda: self.chat(divine_prompt)   # type: ignore[attr-defined]
            )
            if not isinstance(agent_response, str):
                agent_response = str(agent_response)

            # ── Parse decision ────────────────────────────────────────────────
            resp_upper = agent_response.upper()
            if "FOLLOW" in resp_upper and "PARTIAL" not in resp_upper:
                decision = "FOLLOW"
            elif "PARTIAL" in resp_upper or ("FOLLOW" in resp_upper and "NOT" not in resp_upper and
                                              "IGNORE" in resp_upper):
                decision = "PARTIAL"
            else:
                decision = "IGNORE"

        except Exception as exc:
            agent_response = f"(pipeline error: {exc})"
            decision       = "IGNORE"

        # ── Divine favor adjustment ───────────────────────────────────────────
        favor_delta = {"FOLLOW": 0.05, "PARTIAL": 0.01, "IGNORE": -0.03}.get(decision, 0)
        self.divine_favor = max(0.0, min(1.0, self.divine_favor + favor_delta))

        # ── Episodic memory with high emotional valence ───────────────────────
        emotional_valence = {"FOLLOW": 0.8, "PARTIAL": 0.4, "IGNORE": 0.1}.get(decision, 0.3)
        try:
            self.memory.episodic.store(
                content={
                    "event":    "divine_message",
                    "persona":  persona,
                    "type":     msg_type,
                    "content":  content[:300],
                    "decision": decision,
                    "response": agent_response[:300],
                },
                emotional_valence=emotional_valence,
                tags=["divine", "high_priority", persona.lower()],
            )
        except Exception:
            pass

        # ── Queue for future planner context ─────────────────────────────────
        self._divine_message_queue.append({
            "msg_id":    msg_id,
            "persona":   persona,
            "content":   content[:200],
            "decision":  decision,
            "timestamp": _utc_now_ms(),
        })
        # Keep only last 10 divine messages in queue
        if len(self._divine_message_queue) > 10:
            self._divine_message_queue.pop(0)

        await self._logger.log(
            event_type=WorldEventType.DIVINE_INTERPRETED,
            agent_id=self.agent_id,
            territory_id=self.territory_id,
            data={
                "msg_id":        msg_id,
                "persona":       persona,
                "decision":      decision,
                "divine_favor":  round(self.divine_favor, 4),
                "response_len":  len(agent_response),
                "response_snippet": agent_response[:200],
            },
        )

        return agent_response

    # ── Social interactions ───────────────────────────────────────────────────

    async def interact_with_agent(
        self,
        other_agent_id: str,
        interaction_type: str,
        content: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Handles social interactions between agents.

        Supported interaction_type values
        ----------------------------------
        TEACH    : Transfer knowledge; costs 1 credit, earns 0.5
        TRADE    : Exchange credits; mutual gain possible
        ALLY     : Establish/strengthen alliance; increases trust
        COMPETE  : Competition; trust may decrease, winner gains credits
        MESSAGE  : Send a message; costs 0.5 credits

        Returns dict with: success, trust_delta, credits_delta, event_logged
        """
        if not self.is_alive:
            return {"success": False, "reason": "Agent is not alive."}

        result: Dict[str, Any] = {
            "success":       False,
            "trust_delta":   0.0,
            "credits_delta": 0.0,
            "event_logged":  False,
        }

        current_trust = self.relationships.get(other_agent_id, 0.0)

        if interaction_type == "TEACH":
            cost = CREDIT_COSTS.get("teach", 1.0)
            if self.credits < cost:
                result["reason"] = "Insufficient credits to teach."
                return result
            self.credits -= cost
            # Export a semantic subgraph for the student
            knowledge_transfer = self.memory.semantic.export_subgraph(
                max_nodes=content.get("depth", 10)
            ) if hasattr(self.memory.semantic, "export_subgraph") else {}
            result.update({
                "success":          True,
                "knowledge":        knowledge_transfer,
                "credits_delta":    -cost,
                "trust_delta":      0.05,
            })
            self.relationships[other_agent_id] = min(1.0, current_trust + 0.05)
            self.reputation = min(1.0, self.reputation + 0.01)

        elif interaction_type == "TRADE":
            trade_amount = float(content.get("amount", 1.0))
            if self.credits < trade_amount:
                result["reason"] = "Insufficient credits for trade."
                return result
            self.credits -= trade_amount
            # Trade outcome determined by trust and reputation
            multiplier = 1.0 + (current_trust * 0.2)
            earned = round(trade_amount * multiplier, 4)
            result.update({
                "success":       True,
                "credits_delta": round(earned - trade_amount, 4),
                "trust_delta":   0.02 if earned >= trade_amount else -0.02,
            })
            self.credits += earned
            self.relationships[other_agent_id] = max(-1.0, min(1.0,
                current_trust + result["trust_delta"]))

        elif interaction_type == "ALLY":
            trust_gain = float(content.get("trust_gain", 0.1))
            self.relationships[other_agent_id] = min(1.0, current_trust + trust_gain)
            result.update({
                "success":     True,
                "trust_delta": trust_gain,
            })
            self.reputation = min(1.0, self.reputation + 0.005)

        elif interaction_type == "COMPETE":
            # Simple competition: higher reputation wins
            challenger_rep = float(content.get("challenger_reputation", 0.5))
            my_score = self.reputation + (self._param("risk_tolerance", 0.5) * 0.1)
            if my_score >= challenger_rep:
                prize = float(content.get("prize", 1.0))
                self.credits += prize
                trust_delta  = -0.05
                result.update({"success": True, "credits_delta": prize, "trust_delta": trust_delta})
            else:
                fine = float(content.get("fine", 0.5))
                self.credits    -= fine
                trust_delta      = -0.1
                self.reputation  = max(0.0, self.reputation - 0.02)
                result.update({"success": False, "credits_delta": -fine, "trust_delta": trust_delta})
            self.relationships[other_agent_id] = max(-1.0, min(1.0,
                current_trust + result["trust_delta"]))

        elif interaction_type == "MESSAGE":
            cost = CREDIT_COSTS.get("message", 0.5)
            if self.credits < cost:
                result["reason"] = "Insufficient credits to message."
                return result
            self.credits -= cost
            result.update({
                "success":       True,
                "credits_delta": -cost,
                "trust_delta":   0.01,
            })
            self.relationships[other_agent_id] = min(1.0, current_trust + 0.01)

        else:
            result["reason"] = f"Unknown interaction_type: {interaction_type}"
            return result

        await self._logger.log(
            event_type=WorldEventType.SOCIAL_INTERACTION,
            agent_id=self.agent_id,
            territory_id=self.territory_id,
            data={
                "other_agent_id":  other_agent_id,
                "type":            interaction_type,
                "trust_after":     round(self.relationships.get(other_agent_id, 0.0), 4),
                "credits_delta":   round(result.get("credits_delta", 0.0), 4),
                "credits_balance": round(self.credits, 4),
            },
        )
        result["event_logged"] = True
        return result

    # ── State snapshot ────────────────────────────────────────────────────────

    def get_state_snapshot(self) -> Dict[str, Any]:
        """
        Full serialisable state for SnapshotEngine.
        All floats rounded; no circular references.
        """
        return {
            # Identity
            "agent_id":    self.agent_id,
            "name":        self.name,
            "generation":  self.generation,
            "parent_id":   self.parent_id,
            "lineage_id":  self.lineage_id,
            "born_at":     self.born_at,
            "territory_id": self.territory_id,
            # Genome summary
            "genome": (
                self.genome.to_dict()
                if hasattr(self.genome, "to_dict")
                else {"capability_bits": str(self.genome.capability_chromosome)[:50]}
            ),
            # Vitals
            "credits":     round(self.credits, 4),
            "age":         self.age,
            "max_age":     self.max_age,
            "health":      round(self.health, 2),
            "status":      self.status.value if hasattr(self.status, "value") else str(self.status),
            # Social
            "reputation":  round(self.reputation, 4),
            "divine_favor": round(self.divine_favor, 4),
            "relationship_count": len(self.relationships),
            "top_allies": [
                {"id": aid, "trust": round(t, 3)}
                for aid, t in sorted(self.relationships.items(), key=lambda kv: kv[1], reverse=True)[:5]
            ],
            # Performance
            "tick_count":       self._tick_count,
            "tasks_total":      len(self._task_history),
            "recent_errors":    self._recent_errors,
            "recent_successes": self._recent_successes,
            "active_tools":     self.active_tools,
            # Memory summary
            "memory_summary": (
                self.memory.summary()
                if hasattr(self.memory, "summary")
                else {"episodic_nodes": "?", "semantic_nodes": "?"}
            ),
            "snapshot_at": _utc_now_ms(),
        }

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<AgentCell id={self.agent_id[:8]} name={self.name!r} "
            f"gen={self.generation} status={self.status} "
            f"credits={self.credits:.2f} age={self.age}/{self.max_age}>"
        )
