"""The model host: one g4dn.xlarge running Ollama for the open models (deployment-aws.md §6,
§6.2).

Kept between sessions and stopped by ops.sh; its private IP survives a stop, so the core's
OLLAMA_URL (SSM <stem>/model-host/url) never changes. Admin is SSM only: no key pair,
no SSH port.
"""

from pathlib import Path

from aws_cdk import CfnParameter, Fn, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from constructs import Construct
from mothusi_infra.network import MODEL_HOST_SUBNET
from mothusi_infra.stage import Stage

# The Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04), from AWS's public SSM parameter
# (documented on the DLAMI release page; resolved by CloudFormation at deploy, no AMI id here).
DLAMI_PARAMETER = (
    "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
)
USER_DATA = Path(__file__).with_name("model_host_user_data.sh")


def user_data(stage: Stage) -> str:
    """The boot script with the stage's stem. It must stay byte-identical to what the running
    host booted with: any change to an instance's user data stops and starts it."""
    stem = stage.adopted
    return (
        USER_DATA.read_text()
        .replace("mothusi", stem)
        .replace("Mothusi", stem.title())
        .replace("MOTHUSI", stem.upper())
    )


class MothusiModelHost(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: Stage, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        def parameter(suffix):
            return ssm.StringParameter.value_for_string_parameter(self, stage.ssm(suffix))

        ami = CfnParameter(
            self,
            "ModelHostAmi",
            type="AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>",
            default=DLAMI_PARAMETER,
            description="Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04), latest",
        )
        role = iam.Role(
            self,
            "Role",
            role_name=f"{stage.prefix}-model-host",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
            ],
        )
        profile = iam.CfnInstanceProfile(
            self,
            "Profile",
            instance_profile_name=f"{stage.prefix}-model-host",
            roles=[role.role_name],
        )
        template = ec2.CfnLaunchTemplate(
            self,
            "LaunchTemplate",
            launch_template_name=f"{stage.prefix}-model-host",
            launch_template_data=ec2.CfnLaunchTemplate.LaunchTemplateDataProperty(
                metadata_options=ec2.CfnLaunchTemplate.MetadataOptionsProperty(
                    http_tokens="required", http_endpoint="enabled", http_put_response_hop_limit=1
                ),
                block_device_mappings=[
                    ec2.CfnLaunchTemplate.BlockDeviceMappingProperty(
                        device_name="/dev/sda1",  # the Ubuntu DLAMI's root device
                        ebs=ec2.CfnLaunchTemplate.EbsProperty(
                            volume_size=100,
                            volume_type="gp3",
                            encrypted=True,
                            kms_key_id=parameter("kms-key-arn"),
                            delete_on_termination=True,
                        ),
                    )
                ],
            ),
        )
        instance = ec2.CfnInstance(
            self,
            "Instance",
            instance_type="g4dn.xlarge",
            image_id=ami.value_as_string,
            launch_template=ec2.CfnInstance.LaunchTemplateSpecificationProperty(
                launch_template_id=template.ref, version=template.attr_latest_version_number
            ),
            # The af-south-1b public subnet: g4dn.xlarge is not offered in 1a. Public subnets map
            # public IPs, so the first boot can download Ollama and the models without a NAT
            # gateway (there is none, §4).
            subnet_id=Fn.select(MODEL_HOST_SUBNET, Fn.split(",", parameter("subnet-ids"))),
            security_group_ids=[parameter("sg/model")],
            iam_instance_profile=profile.ref,
            user_data=Fn.base64(user_data(stage)),
            tags=[{"key": "Name", "value": f"{stage.label}-model-host"}],
        )
        for suffix, value in {
            "model-host/instance-id": instance.ref,
            "model-host/url": Fn.join("", ["http://", instance.attr_private_ip, ":11434"]),
        }.items():
            ssm.StringParameter(
                self, f"Output-{suffix}", parameter_name=stage.ssm(suffix), string_value=value
            )
