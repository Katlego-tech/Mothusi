"""Release-scoped Fargate services with SSM-only cross-stack wiring (§4, §6.4)."""

from typing import cast

from aws_cdk import ArnFormat, Fn, Stack
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_servicediscovery as discovery
from aws_cdk import aws_ssm as ssm
from aws_cdk import custom_resources as cr
from constructs import Construct
from mothusi_infra.stage import ECR_SERVICES, Stage


class MothusiService(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: Stage, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        selected = self.node.try_get_context("services") or ""
        names = list(dict.fromkeys(n.strip() for n in selected.split(",") if n.strip()))
        if set(names) - set(ECR_SERVICES):
            raise ValueError("services must list only core,intake,knowledge")
        tag = self.node.try_get_context("imageTag")
        if names and not tag:
            raise ValueError("imageTag is required when services are selected")

        def parameter(suffix):
            return ssm.StringParameter.value_for_string_parameter(self, stage.ssm(suffix))

        def bucket_arn(name):
            return Fn.join("", ["arn:", self.partition, ":s3:::", parameter(f"bucket/{name}")])

        def secret_arn(name):
            return self.format_arn(
                service="secretsmanager",
                resource="secret",
                resource_name=stage.secret(f"{name}-??????"),
                arn_format=ArnFormat.COLON_RESOURCE_NAME,
            )

        trust = iam.ServicePrincipal(
            "ecs-tasks.amazonaws.com",
            conditions={
                "StringEquals": {"aws:SourceAccount": self.account},
                "ArnLike": {
                    "aws:SourceArn": self.format_arn(
                        service="ecs", resource="*", arn_format=ArnFormat.NO_RESOURCE_NAME
                    )
                },
            },
        )
        # CDK principal protocol uses different parameter names.
        execution = iam.Role(
            self,
            "ExecutionRole",
            role_name=f"{stage.prefix}-task-execution",
            assumed_by=cast(iam.IPrincipal, trust),
        )
        execution.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        execution.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                ],
                resources=[
                    self.format_arn(
                        service="ecr", resource="repository", resource_name=f"{stage.prefix}-{n}"
                    )
                    for n in ECR_SERVICES
                ],
            )
        )
        execution.add_to_policy(
            iam.PolicyStatement(actions=["kms:Decrypt"], resources=[parameter("kms-key-arn")])
        )
        cluster = ecs.CfnCluster(self, "Cluster", cluster_name=stage.prefix)
        service_names = []
        for name in names:
            log = logs.LogGroup(
                self,
                f"{name}Logs",
                log_group_name=f"/{stage.label}/{name}",
                retention=logs.RetentionDays.ONE_WEEK,
            )
            execution.add_to_policy(
                iam.PolicyStatement(
                    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                    resources=[log.log_group_arn],
                )
            )
            role = iam.Role(
                self,
                f"{name}TaskRole",
                role_name=f"{stage.prefix}-{name}-task",
                assumed_by=cast(iam.IPrincipal, trust),
            )
            env = {"AWS_REGION": self.region}
            secret_values = {}
            if name in ("core", "intake"):
                env.update(
                    OLLAMA_URL=parameter("model-host/url"),
                    MOTHUSI_DATA="/data",
                    MOTHUSI_UPLOAD_BUCKET=parameter("bucket/uploads"),
                )
            if name in ("core", "knowledge"):
                # The generated RDS secret holds host, port, username and password; the database
                # name is fixed by MothusiData (stage.db_name), so it is plain.
                env["POSTGRES_DB"] = stage.db_name
                db_secret = parameter("db/secret-arn")
                secret_values.update(
                    {
                        f"POSTGRES_{env_name}": Fn.join("", [db_secret, f":{key}::"])
                        for env_name, key in (
                            ("HOST", "host"),
                            ("PORT", "port"),
                            ("USER", "username"),
                            ("PASSWORD", "password"),
                        )
                    }
                )
                execution.add_to_policy(
                    iam.PolicyStatement(
                        actions=["secretsmanager:GetSecretValue"], resources=[db_secret]
                    )
                )
            if name == "core":
                public_url = Fn.join("", ["https://", parameter("front/distribution-domain")])
                env.update(
                    MOTHUSI_PROVIDER="ollama",
                    MOTHUSI_OLLAMA_MODEL="qwen3:14b",
                    MOTHUSI_OLLAMA_MODERATOR_MODEL="phi4",
                    MOTHUSI_OLLAMA_READER_MODEL="qwen3-vl:8b-instruct",
                    MOTHUSI_BUNDLE_BUCKET=parameter("bucket/bundles"),
                    MOTHUSI_WEB_ORIGINS=public_url,
                    MOTHUSI_PUBLIC_URL=public_url,
                    MOTHUSI_NOTE_LINK_DAYS="14",
                    COGNITO_USER_POOL_ID=parameter("cognito/user-pool-id"),
                    COGNITO_CLIENT_ID=parameter("cognito/client-id"),
                )
                if "knowledge" in names:
                    env["KNOWLEDGE_URL"] = f"http://knowledge.{stage.namespace}:8000"
                note_lookup = cr.AwsCustomResource(
                    self,
                    "NoteKeyArn",
                    install_latest_aws_sdk=False,
                    on_update=cr.AwsSdkCall(
                        service="SecretsManager",
                        action="describeSecret",
                        parameters={"SecretId": stage.secret("note-key")},
                        physical_resource_id=cr.PhysicalResourceId.of(stage.secret("note-key")),
                        output_paths=["ARN"],
                    ),
                    policy=cr.AwsCustomResourcePolicy.from_statements(
                        [
                            iam.PolicyStatement(
                                actions=["secretsmanager:DescribeSecret"],
                                resources=[secret_arn("note-key")],
                            )
                        ]
                    ),
                )
                note_secret = note_lookup.get_response_field("ARN")
                secret_values["MOTHUSI_NOTE_KEY"] = note_secret
                readable = [secret_arn("note-key")]
                # The ElevenLabs secret is filled by hand; ECS cannot start a task whose secret is
                # empty, so the key is injected only once the voice is switched on (-c voice=true).
                if self.node.try_get_context("voice") == "true":
                    elevenlabs = parameter("secret/elevenlabs-arn")
                    secret_values["ELEVENLABS_API_KEY"] = elevenlabs
                    readable.append(elevenlabs)
                execution.add_to_policy(
                    iam.PolicyStatement(
                        actions=["secretsmanager:GetSecretValue"], resources=readable
                    )
                )
                for bucket in ("uploads", "bundles"):
                    arn = bucket_arn(bucket)
                    role.add_to_policy(
                        iam.PolicyStatement(actions=["s3:ListBucket"], resources=[arn])
                    )
                    role.add_to_policy(
                        iam.PolicyStatement(
                            actions=["s3:GetObject", "s3:PutObject"], resources=[arn + "/*"]
                        )
                    )
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["s3:DeleteObject"], resources=[bucket_arn("bundles") + "/*"]
                    )
                )
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["kms:Decrypt", "kms:GenerateDataKey"],
                        resources=[parameter("kms-key-arn")],
                    )
                )
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["cognito-idp:AdminGetUser"],
                        resources=[
                            self.format_arn(
                                service="cognito-idp",
                                resource="userpool",
                                resource_name=parameter("cognito/user-pool-id"),
                            )
                        ],
                    )
                )
            elif name == "intake":
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["s3:ListBucket"], resources=[bucket_arn("uploads")]
                    )
                )
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["s3:GetObject"], resources=[bucket_arn("uploads") + "/*"]
                    )
                )
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["kms:Decrypt"], resources=[parameter("kms-key-arn")]
                    )
                )
            else:
                env["KNOWLEDGE_SNAPSHOT"] = parameter("knowledge/snapshot")
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["s3:GetObject"], resources=[bucket_arn("curriculum") + "/*"]
                    )
                )
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["secretsmanager:GetSecretValue"],
                        resources=[parameter("db/secret-arn")],
                    )
                )
                role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["kms:Decrypt"],
                        resources=[parameter("kms-key-arn")],
                        conditions={
                            "StringEquals": {"kms:ViaService": f"s3.{self.region}.amazonaws.com"}
                        },
                    )
                )
            cpu, memory = {"core": (1024, 2048), "intake": (512, 1024), "knowledge": (1024, 4096)}[
                name
            ]
            task = ecs.CfnTaskDefinition(
                self,
                f"{name}Task",
                family=f"{stage.label}-{name}",
                cpu=str(cpu),
                memory=str(memory),
                network_mode="awsvpc",
                requires_compatibilities=["FARGATE"],
                runtime_platform=ecs.CfnTaskDefinition.RuntimePlatformProperty(
                    cpu_architecture="X86_64", operating_system_family="LINUX"
                ),
                execution_role_arn=execution.role_arn,
                task_role_arn=role.role_arn,
                container_definitions=[
                    ecs.CfnTaskDefinition.ContainerDefinitionProperty(
                        name=name,
                        image=Fn.join("", [parameter(f"ecr/{name}"), ":", tag]),
                        essential=True,
                        port_mappings=[
                            ecs.CfnTaskDefinition.PortMappingProperty(
                                container_port=8000, protocol="tcp"
                            )
                        ],
                        environment=[
                            ecs.CfnTaskDefinition.KeyValuePairProperty(name=k, value=v)
                            for k, v in env.items()
                        ],
                        secrets=[
                            ecs.CfnTaskDefinition.SecretProperty(name=k, value_from=v)
                            for k, v in secret_values.items()
                        ]
                        or None,
                        log_configuration=ecs.CfnTaskDefinition.LogConfigurationProperty(
                            log_driver="awslogs",
                            options={
                                "awslogs-group": log.log_group_name,
                                "awslogs-region": self.region,
                                "awslogs-stream-prefix": name,
                            },
                        ),
                    )
                ],
            )
            registry = discovery.CfnService(
                self,
                f"{name}Discovery",
                name=name,
                namespace_id=parameter("namespace-id"),
                dns_config=discovery.CfnService.DnsConfigProperty(
                    dns_records=[discovery.CfnService.DnsRecordProperty(type="A", ttl=10)],
                    routing_policy="MULTIVALUE",
                ),
                health_check_custom_config=discovery.CfnService.HealthCheckCustomConfigProperty(
                    failure_threshold=1
                ),
            )
            service = ecs.CfnService(
                self,
                f"{name}Service",
                service_name=name,
                cluster=cluster.ref,
                task_definition=task.ref,
                launch_type="FARGATE",
                desired_count=1,
                deployment_configuration=ecs.CfnService.DeploymentConfigurationProperty(
                    maximum_percent=100, minimum_healthy_percent=0
                ),
                network_configuration=ecs.CfnService.NetworkConfigurationProperty(
                    awsvpc_configuration=ecs.CfnService.AwsVpcConfigurationProperty(
                        assign_public_ip="ENABLED",
                        subnets=Fn.split(",", parameter("subnet-ids")),
                        security_groups=[parameter("sg/service")],
                    )
                ),
                service_registries=[
                    ecs.CfnService.ServiceRegistryProperty(registry_arn=registry.attr_arn)
                ],
                load_balancers=[
                    ecs.CfnService.LoadBalancerProperty(
                        container_name=name,
                        container_port=8000,
                        target_group_arn=parameter("front/core-target-group-arn"),
                    )
                ]
                if name == "core"
                else None,
            )
            # IAM policies must exist before ECS starts tasks (role Ref alone is insufficient).
            service.node.add_dependency(execution, role)
            if name == "core":
                service.node.add_dependency(note_lookup)  # pyright: ignore[reportPossiblyUnboundVariable] -- The same name == "core" guard creates it above.
            service_names.append(name)
        for suffix, value in {
            "cluster-name": cluster.ref,
            **({"service-names": ",".join(service_names)} if service_names else {}),
        }.items():
            ssm.StringParameter(
                self, f"Output-{suffix}", parameter_name=stage.ssm(suffix), string_value=value
            )
