"""Account-level CI: the shared image repositories and one GitHub deploy role per stage.

Both stages run images from the same ECR repositories, so production deploys exactly the image
tag staging tested. Each stage's role is assumed only from that GitHub environment
(repo:<owner>/<repo>:environment:<stage>), so a workflow job can reach production only through
the `production` environment and its required reviewer (deployment-aws.md §4).
"""

from aws_cdk import ArnFormat, CfnParameter, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from constructs import Construct
from mothusi_infra.stage import ECR_SERVICES, STAGES, shared_ssm


class MothusiCi(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        repository = CfnParameter(
            self,
            "GitHubRepository",
            type="String",
            allowed_pattern=r"[\w.-]+/[\w.-]+",
            description="GitHub owner/repository whose environments may deploy.",
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

        repos = {
            name: ecr.Repository(
                self,
                f"{name.title()}Repository",
                repository_name=f"mothusi-{name}",
                lifecycle_rules=[ecr.LifecycleRule(max_image_count=20)],
                removal_policy=RemovalPolicy.RETAIN,
            )
            for name in ECR_SERVICES
        }
        for name, repo in repos.items():
            ssm.StringParameter(
                self,
                f"Output-ecr/{name}",
                parameter_name=shared_ssm(f"ecr/{name}"),
                string_value=repo.repository_uri,
            )
        repo_arns = [repo.repository_arn for repo in repos.values()]

        for stage in STAGES.values():
            subject = f"repo:{repository.value_as_string}:environment:{stage.name}"
            role = iam.Role(
                self,
                f"{stage.title}DeployRole",
                role_name=f"{stage.prefix}-github-deploy",
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

            def allow(actions, resource_arns, role=role):
                role.add_to_policy(iam.PolicyStatement(actions=actions, resources=resource_arns))

            allow(["ecr:GetAuthorizationToken"], ["*"])
            pull = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"]
            if stage.name == "staging":
                # Staging builds and pushes the image; production only deploys a tag that exists.
                pull += [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:PutImage",
                ]
            allow(pull, repo_arns)
            allow(
                ["cloudformation:*"],
                [
                    self.format_arn(
                        service="cloudformation",
                        resource="stack",
                        resource_name=f"{stage.stack_prefix}*/*",
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
            # deploy.yml reads this stage's console bucket, distribution and Cognito values.
            allow(
                ["ssm:GetParameter"],
                [
                    self.format_arn(
                        service="ssm",
                        resource="parameter",
                        resource_name=stage.ssm("*").lstrip("/"),
                    ),
                    self.format_arn(
                        service="ssm", resource="parameter", resource_name="mothusi/ecr/*"
                    ),
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
                        resource_name="*",  # the Front stacks deploy later; this account only
                        arn_format=ArnFormat.SLASH_RESOURCE_NAME,
                    )
                ],
            )
            ssm.StringParameter(
                self,
                f"{stage.title}DeployRoleArn",
                parameter_name=stage.ssm("ci/deploy-role-arn"),
                string_value=role.role_arn,
            )
