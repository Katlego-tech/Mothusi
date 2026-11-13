"""The GitHub release role (deployment-aws.md §4).

Assumed only by jobs in the repository's `production` GitHub environment, which only release
tags (v*) may deploy to: repo:<owner>/<repo>:environment:production. It pushes the release's
images, redeploys the service stack and publishes the console.
"""

from aws_cdk import ArnFormat, CfnParameter, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from constructs import Construct
from mothusi_infra.stage import ECR_SERVICES, Stage


class MothusiCi(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: Stage, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        repository = CfnParameter(
            self,
            "GitHubRepository",
            type="String",
            # GitHub's immutable subject carries the numeric IDs (owner@id/repo@id), so a renamed or
            # recreated repository can't take the role over. Read the repository's prefix with:
            #   gh api repos/<owner>/<repo>/actions/oidc/customization/sub
            allowed_pattern=r"[\w.-]+(@\d+)?/[\w.-]+(@\d+)?",
            description="The repository part of GitHub's OIDC subject: owner@id/repo@id.",
        )
        # An account holds one OIDC provider per URL. Katlego's account already has GitHub's
        # (checked 2026-11-10), so it is imported by ARN unless createGithubOidcProvider=true.
        if self.node.try_get_context("createGithubOidcProvider") == "true":
            provider_arn = iam.CfnOIDCProvider(
                self,
                "GitHubOidc",
                url="https://token.actions.githubusercontent.com",
                client_id_list=["sts.amazonaws.com"],
            ).attr_arn
        else:
            provider_arn = self.format_arn(
                service="iam",
                region="",
                resource="oidc-provider",
                resource_name="token.actions.githubusercontent.com",
                arn_format=ArnFormat.SLASH_RESOURCE_NAME,
            )
        subject = f"repo:{repository.value_as_string}:environment:{stage.name}"
        role = iam.Role(
            self,
            "DeployRole",
            role_name=f"{stage.label}-github-release",
            assumed_by=iam.FederatedPrincipal(
                provider_arn,
                {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": subject,
                    }
                },
                "sts:AssumeRoleWithWebIdentity",
            ),
        )

        def allow(actions, resource_arns):
            role.add_to_policy(iam.PolicyStatement(actions=actions, resources=resource_arns))

        allow(["ecr:GetAuthorizationToken"], ["*"])
        allow(
            [
                "ecr:BatchCheckLayerAvailability",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            ],
            [
                self.format_arn(
                    service="ecr", resource="repository", resource_name=f"{stage.prefix}-{name}"
                )
                for name in ECR_SERVICES
            ],
        )
        allow(
            ["cloudformation:*"],
            [
                self.format_arn(
                    service="cloudformation",
                    resource="stack",
                    resource_name=f"{stage.stack('Service')}/*",
                    arn_format=ArnFormat.SLASH_RESOURCE_NAME,
                )
            ],
        )
        allow(
            ["sts:AssumeRole"],
            [
                self.format_arn(
                    service="iam",
                    region="",
                    resource="role",
                    resource_name=f"cdk-*-{self.region}",
                    arn_format=ArnFormat.SLASH_RESOURCE_NAME,
                )
            ],
        )
        # release.yml reads the console bucket, distribution and Cognito values from SSM.
        allow(
            ["ssm:GetParameter"],
            [
                self.format_arn(
                    service="ssm", resource="parameter", resource_name=stage.ssm("*").lstrip("/")
                )
            ],
        )
        # Read-only, for the diagnostics a failed release prints (.github/scripts/
        # release-diagnostics.sh): the services' events, why tasks stopped, and their logs.
        allow(["ecs:DescribeServices", "ecs:ListTasks", "ecs:DescribeTasks"], ["*"])
        allow(
            ["logs:FilterLogEvents", "logs:DescribeLogStreams", "logs:DescribeLogGroups"],
            [
                self.format_arn(
                    service="logs",
                    resource="log-group",
                    resource_name=f"/{stage.label}/*",
                    arn_format=ArnFormat.COLON_RESOURCE_NAME,
                )
            ],
        )
        console = f"arn:{self.partition}:s3:::{stage.prefix}-console-{self.account}"
        allow(["s3:ListBucket"], [console])
        allow(["s3:PutObject", "s3:DeleteObject"], [f"{console}/*"])
        allow(
            ["cloudfront:CreateInvalidation"],
            [
                self.format_arn(
                    service="cloudfront",
                    region="",
                    resource="distribution",
                    resource_name="*",  # this account's distributions only
                    arn_format=ArnFormat.SLASH_RESOURCE_NAME,
                )
            ],
        )
        ssm.StringParameter(
            self,
            "DeployRoleArn",
            parameter_name=stage.ssm("ci/deploy-role-arn"),
            string_value=role.role_arn,
        )
