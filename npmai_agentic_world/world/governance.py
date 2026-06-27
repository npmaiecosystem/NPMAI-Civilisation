"""
world/governance.py
===================
RID (Representative Ideal Democracy) governance layer for the NPMAI Agentic World.

Proposal   — a law proposal open for voting
Election   — a representative election event
RIDInstance — per-territory governance engine; one instance per Territory

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 5 (world layer)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.constants import WORLD_CONSTANTS, CREDIT_COSTS, AgentStatus, DeathMode
from data.event_logger import EventLogger
from data.event_types import WorldEventType


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Proposal:
    """A governance proposal submitted by any agent, voted on by all."""
    proposal_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    title:         str = ""
    rule_text:     str = ""
    proposed_by:   str = ""   # agent_id
    proposed_at:   int = field(default_factory=_utc_now_ms)
    votes_for:     Dict[str, float] = field(default_factory=dict)   # agent_id → credit_weight
    votes_against: Dict[str, float] = field(default_factory=dict)
    status:        str = "OPEN"     # OPEN | PASSED | FAILED | EXPIRED
    expires_at:    int = 0          # tick number

    def total_for(self) -> float:
        return sum(self.votes_for.values())

    def total_against(self) -> float:
        return sum(self.votes_against.values())

    def total_cast(self) -> float:
        return self.total_for() + self.total_against()

    def voter_count(self) -> int:
        return len(self.votes_for) + len(self.votes_against)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id":   self.proposal_id,
            "title":         self.title,
            "rule_text":     self.rule_text,
            "proposed_by":   self.proposed_by,
            "proposed_at":   self.proposed_at,
            "votes_for":     self.votes_for,
            "votes_against": self.votes_against,
            "total_for":     round(self.total_for(), 4),
            "total_against": round(self.total_against(), 4),
            "status":        self.status,
            "expires_at":    self.expires_at,
        }


@dataclass
class Election:
    """A representative election held every 500 ticks."""
    election_id:  str = field(default_factory=lambda: str(uuid.uuid4()))
    territory_id: str = ""
    candidates:   List[str] = field(default_factory=list)    # agent_ids
    votes:        Dict[str, str] = field(default_factory=dict)  # voter_id → candidate_id
    vote_weights: Dict[str, float] = field(default_factory=dict)  # voter_id → credit_weight
    result:       Optional[str] = None   # winning agent_id
    held_at:      int = field(default_factory=_utc_now_ms)

    def tally(self) -> Dict[str, float]:
        """Returns {candidate_id: total_weight}."""
        tally: Dict[str, float] = {c: 0.0 for c in self.candidates}
        for voter_id, candidate_id in self.votes.items():
            if candidate_id in tally:
                tally[candidate_id] += self.vote_weights.get(voter_id, 1.0)
        return tally

    def winner(self) -> Optional[str]:
        tally = self.tally()
        if not tally:
            return None
        return max(tally, key=tally.get)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "election_id":  self.election_id,
            "territory_id": self.territory_id,
            "candidates":   self.candidates,
            "votes":        self.votes,
            "tally":        {k: round(v, 4) for k, v in self.tally().items()},
            "result":       self.result,
            "held_at":      self.held_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# RIDInstance
# ─────────────────────────────────────────────────────────────────────────────

class RIDInstance:
    """
    Representative Ideal Democracy engine for a single territory.

    Design
    ------
    - Any agent can submit a proposal.
    - Voting power = credits held at time of vote.
    - Quorum = total voting power >= 30% of territory's total credit holdings.
    - Pass threshold = 51% of total weight cast.
    - Proposal voting period = WORLD_CONSTANTS["proposal_voting_ticks"] (default 100).
    - Elections every 500 ticks; all alive agents can run and vote.
    - Laws are enforced by the Auditor LLM inside execute_task(); the
      `enforce_laws` method here is the pre-check / flagging layer.
    """

    # governance config (tunable via WORLD_CONSTANTS)
    _PROPOSAL_TICKS:  int   = int(WORLD_CONSTANTS.get("proposal_voting_ticks", 100))
    _ELECTION_TICKS:  int   = int(WORLD_CONSTANTS.get("election_interval_ticks", 500))
    _QUORUM_FRACTION: float = float(WORLD_CONSTANTS.get("quorum_fraction", 0.30))
    _PASS_THRESHOLD:  float = float(WORLD_CONSTANTS.get("pass_threshold", 0.51))
    _EXECUTION_TICKS: int   = int(WORLD_CONSTANTS.get("execution_vote_ticks", 50))

    def __init__(self, territory_id: str) -> None:
        self.territory_id:      str = territory_id
        self.representatives:   List[str] = []       # agent_ids of elected reps
        self.proposals:         List[Proposal] = []
        self.vote_history:      List[Dict[str, Any]] = []
        self.election_history:  List[Election] = []
        self._current_tick:     int = 0
        self._logger = EventLogger.get_instance()

    # ── Proposal lifecycle ────────────────────────────────────────────────────

    async def submit_proposal(
        self,
        agent_id: str,
        title: str,
        rule_text: str,
    ) -> Proposal:
        """
        Any agent can submit a proposal. Opens a voting period.
        """
        proposal = Proposal(
            title=title,
            rule_text=rule_text,
            proposed_by=agent_id,
            expires_at=self._current_tick + self._PROPOSAL_TICKS,
        )
        self.proposals.append(proposal)

        await self._logger.log(
            event_type=WorldEventType.PROPOSAL_CREATED,
            agent_id=agent_id,
            territory_id=self.territory_id,
            data={
                "proposal_id": proposal.proposal_id,
                "title":       title,
                "rule_text":   rule_text[:300],
                "expires_at":  proposal.expires_at,
            },
        )
        return proposal

    async def cast_vote(
        self,
        agent_id: str,
        proposal_id: str,
        in_favor: bool,
        agent_credits: float,
    ) -> bool:
        """
        Cast a vote on an open proposal.
        Voting power = agent's current credit balance.
        Each agent can vote at most once per proposal.
        """
        proposal = self._get_proposal(proposal_id)
        if proposal is None or proposal.status != "OPEN":
            return False

        # Prevent double-voting
        if agent_id in proposal.votes_for or agent_id in proposal.votes_against:
            return False

        weight = max(0.0, agent_credits)
        if in_favor:
            proposal.votes_for[agent_id] = weight
        else:
            proposal.votes_against[agent_id] = weight

        self.vote_history.append({
            "proposal_id": proposal_id,
            "agent_id":    agent_id,
            "in_favor":    in_favor,
            "weight":      round(weight, 4),
            "tick":        self._current_tick,
        })

        await self._logger.log(
            event_type=WorldEventType.VOTE_CAST,
            agent_id=agent_id,
            territory_id=self.territory_id,
            data={
                "proposal_id": proposal_id,
                "in_favor":    in_favor,
                "weight":      round(weight, 4),
            },
        )
        return True

    async def process_proposal_results(
        self,
        proposal_id: str,
        territory: Any,
    ) -> bool:
        """
        Evaluate a proposal when its voting period has ended.

        Quorum  : total weight cast >= 30% of territory's total credit.
        Pass    : votes_for weight > 51% of total cast weight.

        If passed, a TerritoryLaw is added to the territory.
        Returns True if the proposal passed.
        """
        from world.territory import TerritoryLaw   # local import to avoid circular

        proposal = self._get_proposal(proposal_id)
        if proposal is None or proposal.status != "OPEN":
            return False

        # Calculate territory's total credit as quorum denominator
        # Duck-typed: territory may have .resources["credit_pool"] or .credit_pool
        territory_credit = 0.0
        try:
            resources = _attr(territory, "resources", {})
            if isinstance(resources, dict):
                territory_credit = resources.get("credit_pool", 0.0) or 0.0
            else:
                territory_credit = _attr(territory, "credit_pool", 0.0) or 0.0
        except Exception:
            territory_credit = 1.0   # avoid division by zero

        quorum_needed = territory_credit * self._QUORUM_FRACTION
        total_cast    = proposal.total_cast()
        quorum_met    = total_cast >= quorum_needed

        votes_for_pct = proposal.total_for() / max(total_cast, 0.001)
        passed = quorum_met and votes_for_pct > self._PASS_THRESHOLD

        proposal.status = "PASSED" if passed else "FAILED"

        if passed:
            law = TerritoryLaw(
                title=proposal.title,
                rule_text=proposal.rule_text,
                passed_at=_utc_now_ms(),
                vote_count=proposal.voter_count(),
                passed_by=list(proposal.votes_for.keys()),
            )
            if hasattr(territory, "add_law"):
                territory.add_law(law)
            elif hasattr(territory, "laws"):
                territory.laws.append(law)

            await self._logger.log(
                event_type=WorldEventType.LAW_PASSED,
                agent_id=proposal.proposed_by,
                territory_id=self.territory_id,
                data={
                    "proposal_id":   proposal_id,
                    "law_id":        law.law_id,
                    "title":         law.title,
                    "votes_for_pct": round(votes_for_pct * 100, 1),
                    "quorum_met":    quorum_met,
                    "voter_count":   proposal.voter_count(),
                },
            )
        else:
            await self._logger.log(
                event_type=WorldEventType.PROPOSAL_FAILED,
                agent_id=proposal.proposed_by,
                territory_id=self.territory_id,
                data={
                    "proposal_id":   proposal_id,
                    "quorum_met":    quorum_met,
                    "votes_for_pct": round(votes_for_pct * 100, 1),
                    "total_cast":    round(total_cast, 4),
                    "quorum_needed": round(quorum_needed, 4),
                },
            )

        return passed

    # ── Elections ─────────────────────────────────────────────────────────────

    async def hold_election(self, territory: Any, agents: Dict[str, Any]) -> Optional[str]:
        """
        Elect a representative for this territory.
        All alive agents in the territory can run and vote.
        Voting power = credits held.
        Winner (plurality by weight) becomes the sole representative.
        """
        territory_population = _attr(territory, "population", []) or []

        # Candidates: all alive agents in this territory
        candidates: List[str] = []
        for agent_id in territory_population:
            agent = agents.get(str(agent_id))
            if agent is None:
                continue
            status = _attr(agent, "status")
            status_val = status.value if hasattr(status, "value") else str(status)
            if status_val not in ("DEAD",):
                candidates.append(str(agent_id))

        if not candidates:
            return None

        election = Election(
            territory_id=self.territory_id,
            candidates=candidates,
        )

        # Each agent votes for the candidate with the highest reputation
        # (in-universe: they "sense" reputation, a simulation shortcut that
        #  avoids running the full LLM pipeline per vote)
        for agent_id in candidates:
            agent = agents.get(agent_id)
            if agent is None:
                continue
            credits = _attr(agent, "credits", 0.0) or 0.0
            if credits <= 0:
                continue

            # Vote for the highest-reputation candidate they're not themselves
            # (or themselves if everyone has the same rep)
            best_candidate = agent_id
            best_rep = -1.0
            for c_id in candidates:
                c = agents.get(c_id)
                if c is None:
                    continue
                rep = _attr(c, "reputation", 0.0) or 0.0
                if rep > best_rep:
                    best_rep = rep
                    best_candidate = c_id

            election.votes[agent_id]        = best_candidate
            election.vote_weights[agent_id] = credits

        election.result = election.winner()
        self.election_history.append(election)

        if election.result:
            self.representatives = [election.result]   # single representative model

        await self._logger.log(
            event_type=WorldEventType.AGENT_ELECTED,
            agent_id=election.result,
            territory_id=self.territory_id,
            data={
                "election_id":    election.election_id,
                "candidates":     candidates,
                "tally":          election.tally(),
                "voter_count":    len(election.votes),
                "representative": election.result,
            },
        )
        return election.result

    # ── Law enforcement ───────────────────────────────────────────────────────

    async def enforce_laws(
        self,
        agent: Any,
        action: str,
        territory: Any,
    ) -> bool:
        """
        Pre-checks whether `action` violates any active territory law.

        Returns True  → action is allowed.
        Returns False → action is blocked; violation is logged.

        Severe violations (repeated offenders or major breaches) trigger an
        automatic execution proposal submitted by the "territory" governance.
        """
        violated_law = None
        if hasattr(territory, "is_law_violated"):
            violated_law = territory.is_law_violated(action)
        elif hasattr(territory, "laws"):
            # Fallback: manual keyword scan
            action_lower = action.lower()
            for law in (territory.laws or []):
                rule_text = _attr(law, "rule_text", "") or ""
                if any(kw in action_lower for kw in rule_text.lower().split()[:20]):
                    violated_law = law
                    break

        if violated_law is None:
            return True   # clean

        agent_id  = _attr(agent, "agent_id", "unknown")
        law_id    = _attr(violated_law, "law_id", "unknown")
        law_title = _attr(violated_law, "title", "unknown law")

        await self._logger.log(
            event_type=WorldEventType.LAW_VIOLATION,
            agent_id=agent_id,
            territory_id=self.territory_id,
            data={
                "action":    action[:200],
                "law_id":    law_id,
                "law_title": law_title,
                "tick":      self._current_tick,
            },
        )

        # Check for severe/repeat violation → auto execution proposal
        recent_violations = sum(
            1 for h in self.vote_history[-100:]
            if h.get("proposal_id", "").startswith("exec:")
               and h.get("agent_id") == agent_id
        )
        if recent_violations >= 2:
            await self.submit_proposal(
                agent_id="territory_governance",
                title=f"Execution proposal: {agent_id[:8]}",
                rule_text=(
                    f"Agent {agent_id} has violated territory law '{law_title}' "
                    f"repeatedly ({recent_violations} times in 100 ticks). "
                    "Propose execution for the safety of the territory."
                ),
            )

        return False

    # ── Execution vote ────────────────────────────────────────────────────────

    async def vote_execution(
        self,
        accused_agent_id: str,
        reason: str,
        territory: Any,
        agents: Dict[str, Any],
    ) -> bool:
        """
        Emergency governance vote to execute an agent.

        Shorter voting period (50 ticks by default).
        If passed (same quorum/threshold rules), the accused agent's
        status is set to DEAD and LifecycleManager.process_death is called
        with DeathMode.EXECUTION.
        """
        # Submit as a time-limited proposal
        exec_proposal = await self.submit_proposal(
            agent_id="territory_governance",
            title=f"EXECUTION: {accused_agent_id[:8]}",
            rule_text=reason,
        )
        exec_proposal.expires_at = self._current_tick + self._EXECUTION_TICKS

        # Immediate auto-vote: all alive agents vote by reputation/law alignment
        territory_population = _attr(territory, "population", []) or []
        for voter_id in territory_population:
            if str(voter_id) == accused_agent_id:
                continue
            agent = agents.get(str(voter_id))
            if agent is None:
                continue
            status = _attr(agent, "status")
            status_val = status.value if hasattr(status, "value") else str(status)
            if status_val == "DEAD":
                continue

            # Agents vote in favour of execution if accused's reputation < 0.3
            accused = agents.get(accused_agent_id)
            accused_rep = _attr(accused, "reputation", 0.5) if accused else 0.5
            in_favor = accused_rep < 0.3

            await self.cast_vote(
                agent_id=str(voter_id),
                proposal_id=exec_proposal.proposal_id,
                in_favor=in_favor,
                agent_credits=_attr(agent, "credits", 0.0) or 0.0,
            )

        # Evaluate immediately
        passed = await self.process_proposal_results(exec_proposal.proposal_id, territory)

        if passed:
            # Trigger execution death
            accused_agent = agents.get(accused_agent_id)
            if accused_agent is not None:
                try:
                    from core.lifecycle import LifecycleManager
                    from data.gene_bank import GeneBank

                    lm = LifecycleManager()
                    gb = GeneBank()
                    await lm.process_death(
                        agent_cell=accused_agent,
                        mode=DeathMode.EXECUTION,
                        territory=territory,
                        gene_bank=gb,
                    )
                except Exception:
                    # Fallback: set status directly
                    if isinstance(accused_agent, dict):
                        accused_agent["status"] = AgentStatus.DEAD
                    else:
                        accused_agent.status = AgentStatus.DEAD

            await self._logger.log(
                event_type=WorldEventType.AGENT_EXECUTED,
                agent_id=accused_agent_id,
                territory_id=self.territory_id,
                data={
                    "proposal_id": exec_proposal.proposal_id,
                    "reason":      reason[:300],
                    "tick":        self._current_tick,
                },
            )

        return passed

    # ── Tick ──────────────────────────────────────────────────────────────────

    async def tick(self, territory: Any) -> None:
        """
        Called every world tick by WorldController.
        - Expires OPEN proposals whose time is up.
        - Evaluates eligible OPEN proposals.
        """
        self._current_tick += 1

        for proposal in list(self.proposals):
            if proposal.status != "OPEN":
                continue
            if self._current_tick >= proposal.expires_at:
                await self.process_proposal_results(proposal.proposal_id, territory)

    # ── Governance report ─────────────────────────────────────────────────────

    def get_governance_report(self) -> Dict[str, Any]:
        open_proposals   = [p for p in self.proposals if p.status == "OPEN"]
        passed_proposals = [p for p in self.proposals if p.status == "PASSED"]
        failed_proposals = [p for p in self.proposals if p.status == "FAILED"]

        return {
            "territory_id":        self.territory_id,
            "representatives":     self.representatives,
            "open_proposals":      len(open_proposals),
            "passed_proposals":    len(passed_proposals),
            "failed_proposals":    len(failed_proposals),
            "total_proposals":     len(self.proposals),
            "elections_held":      len(self.election_history),
            "last_election_result": (
                self.election_history[-1].to_dict() if self.election_history else None
            ),
            "total_votes_cast":    len(self.vote_history),
            "current_tick":        self._current_tick,
            "active_proposals":    [p.to_dict() for p in open_proposals[:5]],
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        for p in self.proposals:
            if p.proposal_id == proposal_id:
                return p
        return None
