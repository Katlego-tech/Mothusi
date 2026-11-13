"""Retained data and audit resources (deployment-aws.md §6.1); the budget is MothusiBudget."""

import json

from aws_cdk import Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secrets
from aws_cdk import aws_ssm as ssm
from constructs import Construct
from mothusi_infra.stage import ECR_SERVICES, Stage


class MothusiData(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: Stage, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        key = kms.Key(
            self,
            "DataKey",
            alias=f"{stage.label}-data",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        # CloudTrail must be allowed to generate data keys for its encrypted log bucket.
        trail_arn = self.format_arn(
            service="cloudtrail", resource="trail", resource_name=stage.trail_name
        )
        key.add_to_resource_policy(
            iam.PolicyStatement(
                principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
                actions=["kms:GenerateDataKey*", "kms:DescribeKey"],
                resources=["*"],
                conditions={
                    "StringEquals": {"aws:SourceArn": trail_arn, "aws:SourceAccount": self.account}
                },
            )
        )
        buckets = {}
        for name in ("uploads", "bundles", "console", "exhibits", "curriculum", "trail"):
            buckets[name] = s3.Bucket(
                self,
                f"{name.title()}Bucket",
                bucket_name=f"{stage.prefix}-{name}-{self.account}",
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                encryption=(
                    s3.BucketEncryption.S3_MANAGED if name == "console" else s3.BucketEncryption.KMS
                ),
                encryption_key=None if name == "console" else key,
                enforce_ssl=True,
                removal_policy=RemovalPolicy.RETAIN,
                lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(30))]
                if name == "uploads"
                else None,
            )
        credentials = secrets.Secret(
            self,
            "DbCredentials",
            secret_name=stage.db_secret,
            encryption_key=key,
            generate_secret_string=secrets.SecretStringGenerator(
                secret_string_template=json.dumps(
                    {"username": stage.db_name}, separators=(",", ":")
                ),
                generate_string_key="password",
                exclude_punctuation=True,
                password_length=32,
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        elevenlabs = secrets.CfnSecret(
            self, "ElevenLabs", name=stage.secret("elevenlabs"), kms_key_id=key.key_arn
        )
        elevenlabs.apply_removal_policy(RemovalPolicy.RETAIN)
        # ASCII generation makes each of the 48 characters exactly one byte.
        secrets.Secret(
            self,
            "NoteKey",
            secret_name=stage.secret("note-key"),
            encryption_key=key,
            generate_secret_string=secrets.SecretStringGenerator(
                password_length=48,
                exclude_punctuation=True,
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        subnet_ids = ssm.StringParameter.value_for_string_parameter(self, stage.ssm("subnet-ids"))
        subnet_group = rds.CfnDBSubnetGroup(
            self,
            "DbSubnets",
            db_subnet_group_description="Mothusi database subnets",
            subnet_ids=Fn.split(",", subnet_ids),
        )
        db = rds.CfnDBInstance(
            self,
            "Database",
            db_instance_identifier=stage.db_identifier,
            engine="postgres",
            engine_version="17",
            db_instance_class="db.t4g.small",
            allocated_storage="20",
            storage_type="gp3",
            multi_az=False,
            publicly_accessible=False,
            storage_encrypted=True,
            kms_key_id=key.key_arn,
            db_name=stage.db_name,
            port="5432",
            db_subnet_group_name=subnet_group.ref,
            vpc_security_groups=[
                ssm.StringParameter.value_for_string_parameter(self, stage.ssm("sg/db"))
            ],
            master_username=credentials.secret_value_from_json("username").unsafe_unwrap(),
            master_user_password=credentials.secret_value_from_json("password").unsafe_unwrap(),
            backup_retention_period=7,
            auto_minor_version_upgrade=True,
        )
        db.apply_removal_policy(RemovalPolicy.RETAIN)
        secrets.CfnSecretTargetAttachment(
            self,
            "DbSecretAttachment",
            secret_id=credentials.secret_arn,
            target_id=db.ref,
            target_type="AWS::RDS::DBInstance",
        )
        repos = {
            name: ecr.Repository(
                self,
                f"{name.title()}Repository",
                repository_name=f"{stage.prefix}-{name}",
                lifecycle_rules=[ecr.LifecycleRule(max_image_count=10)],
                removal_policy=RemovalPolicy.RETAIN,
            )
            for name in ECR_SERVICES
        }
        cloudtrail.Trail(
            self,
            "Trail",
            trail_name=stage.trail_name,
            bucket=buckets["trail"],
            encryption_key=key,
            management_events=cloudtrail.ReadWriteType.ALL,
            enable_file_validation=True,
            is_multi_region_trail=False,
        )

        values = {
            "kms-key-arn": key.key_arn,
            **{
                f"bucket/{name}": bucket.bucket_name
                for name, bucket in buckets.items()
                if name != "trail"
            },
            "db/endpoint": db.attr_endpoint_address,
            "db/secret-arn": credentials.secret_arn,
            **{f"ecr/{name}": repo.repository_uri for name, repo in repos.items()},
            "secret/elevenlabs-arn": elevenlabs.ref,
        }
        for suffix, value in values.items():
            ssm.StringParameter(
                self, f"Output-{suffix}", parameter_name=stage.ssm(suffix), string_value=value
            )
