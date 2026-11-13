"""Mothusi's one AWS environment, and every name in it.

The environment already existed when Mothusi adopted it, and it was adopted in place so that
nothing stateful was rebuilt: the GPU host keeps running with its models loaded. AWS can't
rename a CloudFormation stack, and it renames none of these without replacing them: S3 buckets,
secrets, IAM roles,
ECR repositories, security groups, the Cloud Map namespace, the CloudTrail trail, the load
balancer and its target group, the GPU host's launch template and boot script, the Cognito
domain. Those keep the stem they were created with (`adopted`). Every name AWS can change in
place is Mothusi's: log groups, task families, the user pool, the KMS alias, the origin header,
tags.

The database is Mothusi's too, by replacement: RDS can't rename the database inside the server or
its master user, so Mothusi has its own instance (mothusi-db, database and user mothusi, secret
mothusi/db). The services create their own tables on it and the knowledge service reloads its
snapshot from S3. The adopted instance is retained by CloudFormation, not deleted.

SSM parameter names keep the stem too, though SSM itself renames freely: CloudFormation decides a
replacement from the template, and the GPU host's subnet, the load balancer's target group and the
database's security groups are all read from these parameters. A new name there replaces the GPU
host and the target group even when the value behind it is the same.

This file is the only place the adopted stem is written down.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    name: str  # also the GitHub environment that may deploy it
    cidr: str
    adopted: str  # the stem of the names AWS can't change

    @property
    def stack_prefix(self) -> str:
        """CloudFormation stack names: <prefix>Network, <prefix>Data, ..."""
        return self.adopted.title()

    def stack(self, part: str) -> str:
        return f"{self.stack_prefix}{part}"

    @property
    def prefix(self) -> str:
        """Physical names AWS can't change: buckets, roles, the database, the load balancer."""
        return self.adopted

    @property
    def label(self) -> str:
        """Names AWS changes in place."""
        return "mothusi"

    def ssm(self, suffix: str) -> str:
        """An SSM parameter: /<adopted>/<suffix> (see the module docstring for why)."""
        return f"/{self.adopted}/{suffix}"

    def secret(self, suffix: str) -> str:
        """A Secrets Manager name (a new name is a new secret, so it keeps the adopted stem)."""
        return f"{self.adopted}/{suffix}"

    def sg_name(self, role: str) -> str:
        return f"{role}-sg"

    @property
    def sg_description_stem(self) -> str:
        """A security group's description can't change without replacing the group."""
        return self.adopted.title()

    @property
    def namespace(self) -> str:
        return f"{self.adopted}.internal"

    @property
    def db_identifier(self) -> str:
        return f"{self.label}-db"

    @property
    def db_name(self) -> str:
        """The database inside the server, and its master user."""
        return self.label

    @property
    def db_secret(self) -> str:
        return f"{self.label}/db"

    @property
    def trail_name(self) -> str:
        return self.adopted

    @property
    def opt_dir(self) -> str:
        """Where the GPU host's boot script wrote model-ready.sh (changing it reboots the host)."""
        return f"/opt/{self.adopted}"


PRODUCTION = Stage("production", "10.40.0.0/16", adopted="thuto")

ECR_SERVICES = ("core", "intake", "knowledge")


def main() -> None:
    """For the release workflow, which must not write the adopted names down a second time:

    python -m mothusi_infra.stage stack Service   ->  the service stack's name
    python -m mothusi_infra.stage ssm             ->  the SSM prefix, e.g. /<adopted>
    """
    import sys

    match sys.argv[1:]:
        case ["stack", part]:
            print(PRODUCTION.stack(part))
        case ["ssm"]:
            print(PRODUCTION.ssm("").rstrip("/"))
        case _:
            sys.exit("usage: python -m mothusi_infra.stage stack <Part> | ssm")


if __name__ == "__main__":
    main()
