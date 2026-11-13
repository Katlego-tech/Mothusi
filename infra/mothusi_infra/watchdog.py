"""The watchdog: an email within about a minute when the environment stops working.

It watches for the two ways time is lost unnoticed: a deploy that never comes up, and a run that
stops (or waits for the teacher) while the GPU sits idle. It only reports; it never stops,
restarts or changes anything. It runs no code of its own (no Lambda): AWS events and CloudWatch
alarms feed one SNS topic, so it costs a few alarms a month.

What raises an alert (one SNS topic, emailed to the address given at deploy time):

- A stack update that fails or rolls back (CloudFormation events, any Mothusi stack).
- An ECS deployment that fails, or a task that fails to start or whose container exits.
- The GPU host leaving `running` (EC2 state change), or failing its status checks.
- A run that halts, or that waits for the teacher (markers the core logs; mothusi_core.events).

Deploy:  npx cdk deploy MothusiWatchdog -c watchdogEmail=<address>   (then confirm the email)
"""

from aws_cdk import CfnParameter, Duration, Stack
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_ssm as ssm
from constructs import Construct
from mothusi_infra.stage import ECR_SERVICES, Stage

NAMESPACE = "Mothusi/Watchdog"
# The core's markers (mothusi_core.events.RUN_HALTED and NEEDS_TEACHER).
RUN_HALTED = "WATCHDOG_RUN_HALTED"
NEEDS_TEACHER = "WATCHDOG_NEEDS_TEACHER"


class MothusiWatchdog(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: Stage, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        def parameter(suffix: str) -> str:
            return ssm.StringParameter.value_for_string_parameter(self, stage.ssm(suffix))

        email = self.node.try_get_context("watchdogEmail")
        if not email:
            email = CfnParameter(
                self,
                "WatchdogEmail",
                type="String",
                allowed_pattern=r"[^\s@]+@[^\s@]+\.[^\s@]+",
                description="Who the watchdog emails.",
            ).value_as_string
        topic = sns.Topic(self, "Alerts", topic_name=f"{stage.label}-watchdog")
        topic.add_subscription(subs.EmailSubscription(email))
        alarm_action = cw_actions.SnsAction(topic)

        def notify(rule_id: str, pattern: events.EventPattern, message: str) -> None:
            events.Rule(self, rule_id, event_pattern=pattern).add_target(
                targets.SnsTopic(topic, message=events.RuleTargetInput.from_text(message))
            )

        # ------------------------------------------------------------ deploys that don't land
        notify(
            "StackFailed",
            events.EventPattern(
                source=["aws.cloudformation"],
                detail_type=["CloudFormation Stack Status Change"],
                detail={
                    "stack-id": [
                        {"wildcard": f"*:stack/{stage.stack_prefix}*"},
                        {"wildcard": "*:stack/Mothusi*"},
                    ],
                    "status-details": {
                        "status": [{"wildcard": "*ROLLBACK*"}, {"wildcard": "*_FAILED"}]
                    },
                },
            ),
            f"Mothusi watchdog: stack {events.EventField.from_path('$.detail.stack-id')} is "
            f"{events.EventField.from_path('$.detail.status-details.status')}: "
            f"{events.EventField.from_path('$.detail.status-details.status-reason')}",
        )
        cluster = f"arn:{self.partition}:ecs:{self.region}:{self.account}:cluster/{stage.prefix}"
        notify(
            "DeploymentFailed",
            events.EventPattern(
                source=["aws.ecs"],
                detail_type=["ECS Deployment State Change"],
                detail={"eventName": ["SERVICE_DEPLOYMENT_FAILED"]},
                resources=[
                    f"arn:{self.partition}:ecs:{self.region}:{self.account}"
                    f":service/{stage.prefix}/{name}"
                    for name in ECR_SERVICES
                ],
            ),
            f"Mothusi watchdog: ECS deployment failed for "
            f"{events.EventField.from_path('$.resources[0]')}: "
            f"{events.EventField.from_path('$.detail.reason')}",
        )
        notify(
            "TaskFailed",
            events.EventPattern(
                source=["aws.ecs"],
                detail_type=["ECS Task State Change"],
                detail={
                    "clusterArn": [cluster],
                    "lastStatus": ["STOPPED"],
                    # A deploy or scale-in stops tasks on purpose (ServiceSchedulerInitiated).
                    "stopCode": ["TaskFailedToStart", "EssentialContainerExited"],
                },
            ),
            f"Mothusi watchdog: a task of {events.EventField.from_path('$.detail.group')} stopped "
            f"({events.EventField.from_path('$.detail.stopCode')}): "
            f"{events.EventField.from_path('$.detail.stoppedReason')}",
        )

        # ------------------------------------------------------------------- the GPU host
        instance_id = parameter("model-host/instance-id")
        notify(
            "GpuHostState",
            events.EventPattern(
                source=["aws.ec2"],
                detail_type=["EC2 Instance State-change Notification"],
                detail={
                    "instance-id": [instance_id],
                    "state": ["stopping", "stopped", "shutting-down", "terminated"],
                },
            ),
            f"Mothusi watchdog: the GPU host is {events.EventField.from_path('$.detail.state')}.",
        )

        def alarm(alarm_id: str, metric: cw.IMetric, description: str, **kwargs) -> cw.Alarm:
            made = cw.Alarm(
                self,
                alarm_id,
                alarm_name=f"{stage.label}-{alarm_id}",
                alarm_description=f"Mothusi watchdog: {description}",
                metric=metric,
                **kwargs,
            )
            made.add_alarm_action(alarm_action)
            made.add_ok_action(alarm_action)
            return made

        alarm(
            "gpu-status-check",
            cw.Metric(
                namespace="AWS/EC2",
                metric_name="StatusCheckFailed",
                dimensions_map={"InstanceId": instance_id},
                period=Duration.minutes(1),
                statistic="Maximum",
            ),
            "the GPU host is failing its EC2 status checks.",
            threshold=1,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )

        # ------------------------------------------------- runs that stop, leaving the GPU idle
        core_logs = logs.LogGroup.from_log_group_name(self, "CoreLogs", f"/{stage.label}/core")
        for marker, alarm_id, what in (
            (RUN_HALTED, "run-halted", "a run halted. The GPU is idle until it is restarted."),
            (
                NEEDS_TEACHER,
                "run-waiting",
                "a run is waiting for the teacher. The GPU is idle until it is answered.",
            ),
        ):
            filter_ = logs.MetricFilter(
                self,
                f"{alarm_id}-filter",
                log_group=core_logs,
                filter_pattern=logs.FilterPattern.literal(f'"{marker}"'),
                metric_namespace=NAMESPACE,
                metric_name=marker,
                metric_value="1",
            )
            alarm(
                alarm_id,
                filter_.metric(period=Duration.minutes(1), statistic="Sum"),
                f"{what} See the core's log group for the run.",
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            )
