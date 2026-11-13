"""The network (deployment-aws.md §4 and §6.1): the VPC, its security groups and namespace."""

from aws_cdk import CfnParameter, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_servicediscovery as discovery
from aws_cdk import aws_ssm as ssm
from constructs import Construct
from mothusi_infra.stage import Stage

# /mothusi/subnet-ids lists the public subnets in this order. g4dn.xlarge is offered in
# 1b and 1c only, so the model host takes index 1 (model_host.py).
AZS = ["af-south-1a", "af-south-1b"]
MODEL_HOST_SUBNET = AZS.index("af-south-1b")


class MothusiNetwork(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: Stage, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        prefix_list = CfnParameter(
            self,
            "CloudFrontPrefixListId",
            type="String",
            allowed_pattern="pl-[a-f0-9]+",
            description="ID of com.amazonaws.global.cloudfront.origin-facing in af-south-1.",
        )
        vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(stage.cidr),
            availability_zones=AZS,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
            gateway_endpoints={
                "S3": ec2.GatewayVpcEndpointOptions(
                    service=ec2.GatewayVpcEndpointAwsService.S3,
                )
            },
        )
        groups = {
            name: ec2.SecurityGroup(
                self,
                f"{name.title()}SecurityGroup",
                vpc=vpc,
                security_group_name=stage.sg_name(name),
                description=f"{stage.sg_description_stem} {name}",
                disable_inline_rules=True,
            )
            for name in ("alb", "service", "model", "db")
        }
        groups["alb"].add_ingress_rule(
            ec2.Peer.prefix_list(prefix_list.value_as_string), ec2.Port.tcp(80)
        )
        for target, source, port in (
            ("service", "alb", 8000),
            ("service", "service", 8000),
            ("model", "service", 11434),
            ("db", "service", 5432),
        ):
            groups[target].add_ingress_rule(groups[source], ec2.Port.tcp(port))
        namespace = discovery.PrivateDnsNamespace(self, "Namespace", name=stage.namespace, vpc=vpc)
        values = {
            "vpc-id": vpc.vpc_id,
            "subnet-ids": ",".join(subnet.subnet_id for subnet in vpc.public_subnets),
            "namespace-id": namespace.namespace_id,
            **{f"sg/{name}": group.security_group_id for name, group in groups.items()},
        }
        for suffix, value in values.items():
            ssm.StringParameter(
                self, f"Output-{suffix}", parameter_name=stage.ssm(suffix), string_value=value
            )
