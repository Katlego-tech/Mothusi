"""The front door, authentication and upload CORS (deployment-aws.md §6)."""

from pathlib import Path
from typing import cast

from aws_cdk import CustomResource, Duration, Fn, Stack
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_elasticloadbalancingv2 as elb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_ssm as ssm
from aws_cdk import custom_resources as cr
from constructs import Construct
from mothusi_infra.stage import Stage


class MothusiFront(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: Stage, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        def parameter(suffix):
            return ssm.StringParameter.value_for_string_parameter(self, stage.ssm(suffix))

        # SecureString dynamic references are unsupported in custom resources.
        # Generate/read in the provider and mask its response with NoEcho instead.
        secret_writer = lambda_.Function(
            self,
            "OriginSecretWriter",
            runtime=lambda_.Runtime.NODEJS_22_X,
            timeout=Duration.seconds(30),
            handler="index.handler",
            environment={"PARAMETER_NAME": stage.ssm("front/origin-secret")},
            code=lambda_.Code.from_inline("""
const {SSMClient, GetParameterCommand, PutParameterCommand} = require('@aws-sdk/client-ssm');
const {randomBytes} = require('crypto');
const ssm = new SSMClient({});
exports.handler = async (event) => {
    const Name = process.env.PARAMETER_NAME;
    if (event.RequestType === 'Delete') return {PhysicalResourceId: Name};
    let value;
    try {
        const result = await ssm.send(new GetParameterCommand({Name, WithDecryption: true}));
        value = result.Parameter.Value;
    } catch (error) {
        if (error.name !== 'ParameterNotFound') throw error;
        value = randomBytes(36).toString('base64url');
        await ssm.send(new PutParameterCommand({Name, Type: 'SecureString', Value: value}));
    }
    return {PhysicalResourceId: Name, NoEcho: true, Data: {Value: value}};
};
"""),
        )
        secret_writer.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:PutParameter"],
                resources=[
                    self.format_arn(
                        service="ssm",
                        resource="parameter",
                        resource_name=stage.ssm("front/origin-secret").lstrip("/"),
                    )
                ],
            )
        )
        # jsii Function implements IFunction despite generated protocol signature mismatches.
        provider = cr.Provider(
            self, "OriginSecretProvider", on_event_handler=cast(lambda_.IFunction, secret_writer)
        )
        stored_secret = CustomResource(self, "OriginSecret", service_token=provider.service_token)
        header_value = stored_secret.get_att_string("Value")
        alb = elb.CfnLoadBalancer(
            self,
            "Alb",
            name=stage.prefix,
            scheme="internet-facing",
            type="application",
            subnets=[Fn.select(i, Fn.split(",", parameter("subnet-ids"))) for i in range(2)],
            security_groups=[parameter("sg/alb")],
            load_balancer_attributes=[
                elb.CfnLoadBalancer.LoadBalancerAttributeProperty(
                    key="idle_timeout.timeout_seconds",
                    value="3600",
                )
            ],
        )
        target = elb.CfnTargetGroup(
            self,
            "CoreTargetGroup",
            name=f"{stage.prefix}-core",
            port=8000,
            protocol="HTTP",
            target_type="ip",
            vpc_id=parameter("vpc-id"),
            health_check_path="/health/live",
            health_check_protocol="HTTP",
            matcher=elb.CfnTargetGroup.MatcherProperty(http_code="200"),
        )
        listener = elb.CfnListener(
            self,
            "HttpListener",
            load_balancer_arn=alb.ref,
            port=80,
            protocol="HTTP",
            default_actions=[
                elb.CfnListener.ActionProperty(
                    type="fixed-response",
                    fixed_response_config=elb.CfnListener.FixedResponseConfigProperty(
                        status_code="403"
                    ),
                )
            ],
        )
        elb.CfnListenerRule(
            self,
            "CloudFrontOnly",
            listener_arn=listener.ref,
            priority=1,
            conditions=[
                elb.CfnListenerRule.RuleConditionProperty(
                    field="http-header",
                    http_header_config=elb.CfnListenerRule.HttpHeaderConfigProperty(
                        http_header_name="X-Mothusi-Origin",
                        values=[header_value],
                    ),
                )
            ],
            actions=[
                elb.CfnListenerRule.ActionProperty(type="forward", target_group_arn=target.ref)
            ],
        )
        strip = cf.Function(
            self,
            "StripApiPrefix",
            function_name="strip-api-prefix",
            code=cf.FunctionCode.from_file(
                file_path=str(Path(__file__).resolve().parents[1] / "functions/strip-api-prefix.js")
            ),
        )
        spa = cf.Function(
            self,
            "SpaRewrite",
            function_name="spa-rewrite",
            code=cf.FunctionCode.from_inline("""function handler(event) {
    var request = event.request;
    if (request.uri.substring(request.uri.lastIndexOf('/') + 1).indexOf('.') === -1) {
        request.uri = '/index.html';
    }
    return request;
}"""),
        )
        oac = cf.CfnOriginAccessControl(
            self,
            "ConsoleOac",
            origin_access_control_config=cf.CfnOriginAccessControl.OriginAccessControlConfigProperty(
                name=f"{stage.label}-console",
                origin_access_control_origin_type="s3",
                signing_behavior="always",
                signing_protocol="sigv4",
            ),
        )
        dynamic = {
            "target_origin_id": "alb",
            "viewer_protocol_policy": "redirect-to-https",
            "cache_policy_id": cf.CachePolicy.CACHING_DISABLED.cache_policy_id,
            "origin_request_policy_id": (
                cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER.origin_request_policy_id
            ),
            "allowed_methods": ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"],
        }
        distribution = cf.CfnDistribution(
            self,
            "Distribution",
            distribution_config=cf.CfnDistribution.DistributionConfigProperty(
                enabled=True,
                default_root_object="index.html",
                origins=[
                    cf.CfnDistribution.OriginProperty(
                        id="console",
                        domain_name=Fn.join(
                            "",
                            [
                                parameter("bucket/console"),
                                ".s3.",
                                self.region,
                                ".",
                                self.url_suffix,
                            ],
                        ),
                        origin_access_control_id=oac.ref,
                        s3_origin_config=cf.CfnDistribution.S3OriginConfigProperty(
                            origin_access_identity=""
                        ),
                    ),
                    cf.CfnDistribution.OriginProperty(
                        id="alb",
                        domain_name=alb.attr_dns_name,
                        custom_origin_config=cf.CfnDistribution.CustomOriginConfigProperty(
                            origin_protocol_policy="http-only",
                            http_port=80,
                            origin_read_timeout=60,
                        ),
                        origin_custom_headers=[
                            cf.CfnDistribution.OriginCustomHeaderProperty(
                                header_name="X-Mothusi-Origin",
                                header_value=header_value,
                            )
                        ],
                    ),
                ],
                default_cache_behavior=cf.CfnDistribution.DefaultCacheBehaviorProperty(
                    target_origin_id="console",
                    viewer_protocol_policy="redirect-to-https",
                    cache_policy_id=cf.CachePolicy.CACHING_OPTIMIZED.cache_policy_id,
                    compress=True,
                    function_associations=[
                        cf.CfnDistribution.FunctionAssociationProperty(
                            event_type="viewer-request",
                            function_arn=spa.function_arn,
                        )
                    ],
                ),
                cache_behaviors=[
                    cf.CfnDistribution.CacheBehaviorProperty(
                        path_pattern="/api/*",
                        **dynamic,
                        function_associations=[
                            cf.CfnDistribution.FunctionAssociationProperty(
                                event_type="viewer-request",
                                function_arn=strip.function_arn,
                            )
                        ],
                    ),
                    cf.CfnDistribution.CacheBehaviorProperty(path_pattern="/n/*", **dynamic),
                ],
            ),
        )
        # Data owns BucketPolicy already. Merge one named OAC statement at
        # deployment rather than giving two stacks ownership of the same policy.
        policy_writer = lambda_.Function(
            self,
            "ConsolePolicyWriter",
            runtime=lambda_.Runtime.NODEJS_22_X,
            timeout=Duration.seconds(30),
            handler="index.handler",
            code=lambda_.Code.from_inline("""
const {S3Client, GetBucketPolicyCommand, PutBucketPolicyCommand} = require('@aws-sdk/client-s3');
const s3 = new S3Client({});
exports.handler = async (event) => {
    const {Bucket, Statement} = event.ResourceProperties;
    let policy;
    try {
        policy = JSON.parse((await s3.send(new GetBucketPolicyCommand({Bucket}))).Policy);
    } catch (error) {
        if (error.name !== 'NoSuchBucketPolicy') throw error;
        policy = {Version: '2012-10-17', Statement: []};
    }
    policy.Statement = policy.Statement.filter(s => s.Sid !== Statement.Sid);
    if (event.RequestType !== 'Delete') policy.Statement.push(Statement);
    await s3.send(new PutBucketPolicyCommand({Bucket, Policy: JSON.stringify(policy)}));
    return {PhysicalResourceId: Bucket + '-oac'};
};
"""),
        )
        policy_writer.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:GetBucketPolicy", "s3:PutBucketPolicy"],
                resources=[
                    Fn.join("", ["arn:", self.partition, ":s3:::", parameter("bucket/console")])
                ],
            )
        )
        policy_provider = cr.Provider(
            self, "ConsolePolicyProvider", on_event_handler=cast(lambda_.IFunction, policy_writer)
        )
        CustomResource(
            self,
            "ConsolePolicy",
            service_token=policy_provider.service_token,
            properties={
                "Bucket": parameter("bucket/console"),
                "Statement": {
                    "Sid": "MothusiConsoleOac",
                    "Effect": "Allow",
                    "Principal": {"Service": "cloudfront.amazonaws.com"},
                    "Action": "s3:GetObject",
                    "Resource": Fn.join(
                        "", ["arn:", self.partition, ":s3:::", parameter("bucket/console"), "/*"]
                    ),
                    "Condition": {
                        "StringEquals": {
                            "AWS:SourceArn": self.format_arn(
                                service="cloudfront",
                                region="",
                                resource="distribution",
                                resource_name=distribution.ref,
                            )
                        }
                    },
                },
            },
        )
        console_url = Fn.join("", ["https://", distribution.attr_domain_name])
        allowed_origins = [console_url]
        if self.node.try_get_context("devCors") in (True, "true", "1"):
            allowed_origins.append("http://localhost:5173")
        cors_call = cr.AwsSdkCall(
            service="S3",
            action="putBucketCors",
            parameters={
                "Bucket": parameter("bucket/uploads"),
                "CORSConfiguration": {
                    "CORSRules": [
                        {
                            "AllowedOrigins": allowed_origins,
                            "AllowedMethods": ["PUT"],
                            # Permit encryption/checksum headers returned by presigning as well.
                            "AllowedHeaders": ["Content-Type", "x-amz-*"],
                            "MaxAgeSeconds": 3000,
                        }
                    ]
                },
            },
            physical_resource_id=cr.PhysicalResourceId.of(f"{stage.label}-uploads-cors"),
            output_paths=[],
        )
        cr.AwsCustomResource(
            self,
            "UploadsCors",
            on_create=cors_call,
            on_update=cors_call,
            install_latest_aws_sdk=False,
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        actions=["s3:PutBucketCORS"],
                        resources=[
                            Fn.join(
                                "", ["arn:", self.partition, ":s3:::", parameter("bucket/uploads")]
                            )
                        ],
                    )
                ]
            ),
        )
        pool = cognito.CfnUserPool(
            self,
            "Teachers",
            user_pool_name=f"{stage.label}-teachers",
            admin_create_user_config=cognito.CfnUserPool.AdminCreateUserConfigProperty(
                allow_admin_create_user_only=True
            ),
        )
        client = cognito.CfnUserPoolClient(
            self,
            "ConsoleClient",
            user_pool_id=pool.ref,
            generate_secret=False,
            allowed_o_auth_flows_user_pool_client=True,
            allowed_o_auth_flows=["code"],
            allowed_o_auth_scopes=["openid", "email", "profile"],
            supported_identity_providers=["COGNITO"],
            callback_ur_ls=[Fn.join("", [console_url, "/auth/callback"])],
            logout_ur_ls=[console_url + "/"],
        )
        domain_prefix = f"{stage.prefix}-{self.account}"
        cognito.CfnUserPoolDomain(self, "HostedUi", user_pool_id=pool.ref, domain=domain_prefix)
        values = {
            "front/distribution-domain": distribution.attr_domain_name,
            "front/distribution-id": distribution.ref,
            "front/alb-dns": alb.attr_dns_name,
            "front/core-target-group-arn": target.ref,
            "cognito/user-pool-id": pool.ref,
            "cognito/client-id": client.ref,
            "cognito/domain": f"https://{domain_prefix}.auth.{self.region}.amazoncognito.com",
        }
        for suffix, value in values.items():
            ssm.StringParameter(
                self, f"Output-{suffix}", parameter_name=stage.ssm(suffix), string_value=value
            )
