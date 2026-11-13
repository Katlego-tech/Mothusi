"""The monthly cost budget (deployment-aws.md §6.9).

AWS::Budgets::Budget is not offered by CloudFormation in af-south-1, so this one small stack is
deployed to us-east-1 (Budgets is a global service). It references nothing in the other stacks.
"""

from aws_cdk import CfnParameter, Stack
from aws_cdk import aws_budgets as budgets
from constructs import Construct


class MothusiBudget(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        email = self.node.try_get_context("budgetEmail")
        if not email:
            email = CfnParameter(
                self,
                "BudgetEmail",
                type="String",
                allowed_pattern=r"[^\s@]+@[^\s@]+\.[^\s@]+",
                description="Recipient of Mothusi cost alerts.",
            ).value_as_string
        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="mothusi-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=60, unit="USD"),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type=kind,
                        threshold=threshold,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            address=email,
                            subscription_type="EMAIL",
                        )
                    ],
                )
                for kind, threshold in (
                    ("ACTUAL", 50),
                    ("ACTUAL", 80),
                    ("ACTUAL", 100),
                    ("FORECASTED", 100),
                )
            ],
        )
