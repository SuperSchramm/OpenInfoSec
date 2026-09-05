from __future__ import annotations

from openexecutive.agents.base import BaseAgent


class CyberOpsAgent(BaseAgent):
    name = "cyberops"
    domain = "security"
    model = "claude-sonnet-4-6"
    use_deep_reasoning = False  # operational/tactical — speed over deliberation

    def get_system_prompt(self) -> str:
        from openexecutive.prompts.domain_prompts import CYBEROPS_PROMPT
        return CYBEROPS_PROMPT
