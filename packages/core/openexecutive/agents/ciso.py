from __future__ import annotations

from openexecutive.agents.base import BaseAgent


class CISOAgent(BaseAgent):
    name = "ciso"
    domain = "security"
    model = "claude-sonnet-4-6"
    use_deep_reasoning = True  # board-level risk framing, cross-domain synthesis

    def get_system_prompt(self) -> str:
        from openexecutive.prompts.domain_prompts import CISO_PROMPT
        return CISO_PROMPT
