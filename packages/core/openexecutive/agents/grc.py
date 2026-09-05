from __future__ import annotations

from openexecutive.agents.base import BaseAgent


class GRCAgent(BaseAgent):
    name = "grc"
    domain = "security"
    model = "claude-sonnet-4-6"
    use_deep_reasoning = True  # board-level risk framing, cross-domain synthesis

    def get_system_prompt(self) -> str:
        from openexecutive.prompts.domain_prompts import GRC_PROMPT
        return GRC_PROMPT
