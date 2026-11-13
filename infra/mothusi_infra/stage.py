"""Deployment stages: staging and production, side by side in one AWS account.

Every per-stage name is derived here, so a staging deploy can never write to a production
resource: stacks are Mothusi<Stage><Part>, resources mothusi-<stage>-<thing>, SSM parameters
/mothusi/<stage>/<thing> and secrets mothusi/<stage>/<thing>. What both stages share (the
GitHub OIDC trust, the ECR repositories, the budget) lives in account-level stacks with plain
mothusi-* names and /mothusi/<thing> parameters.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    name: str  # "staging" | "production": also the GitHub environment that may deploy it
    cidr: str  # the stage's VPC; distinct, so the two could be peered later

    @property
    def title(self) -> str:
        return self.name.title()

    @property
    def stack_prefix(self) -> str:
        return f"Mothusi{self.title}"

    @property
    def prefix(self) -> str:
        """Physical resource names: mothusi-<stage>."""
        return f"mothusi-{self.name}"

    def ssm(self, suffix: str) -> str:
        """An SSM parameter name: /mothusi/<stage>/<suffix>."""
        return f"/mothusi/{self.name}/{suffix}"

    def secret(self, suffix: str) -> str:
        """A Secrets Manager name: mothusi/<stage>/<suffix>."""
        return f"mothusi/{self.name}/{suffix}"

    @property
    def namespace(self) -> str:
        """The stage's private DNS namespace (Cloud Map)."""
        return f"{self.name}.mothusi.internal"


STAGING = Stage("staging", "10.41.0.0/16")
PRODUCTION = Stage("production", "10.40.0.0/16")
STAGES = {stage.name: stage for stage in (STAGING, PRODUCTION)}

# Account-level names shared by both stages.
ECR_SERVICES = ("core", "intake", "knowledge")


def shared_ssm(suffix: str) -> str:
    """An account-level SSM parameter: /mothusi/<suffix> (never a stage's)."""
    return f"/mothusi/{suffix}"
