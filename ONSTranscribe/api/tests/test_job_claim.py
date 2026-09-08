"""Claim atômico e should_skip."""
from app.services.job_claim import should_skip_status, try_claim_job
from botocore.exceptions import ClientError


def test_should_skip_busy_statuses():
    assert should_skip_status("COMPLETED") is True
    assert should_skip_status("IN_PROGRESS") is True
    assert should_skip_status("PENDING") is False
    assert should_skip_status("FAILED") is False
    assert should_skip_status(None) is False


class _FakeTable:
    def __init__(self, existing=None):
        self.existing = existing
        self.put_calls = []

    def get_item(self, Key):
        if self.existing:
            return {"Item": self.existing}
        return {}

    def put_item(self, **kwargs):
        self.put_calls.append(kwargs)
        cond = kwargs.get("ConditionExpression", "")
        if "IN_PROGRESS" in str(kwargs.get("ExpressionAttributeValues", {})):
            if self.existing and self.existing.get("status") in (
                "IN_PROGRESS",
                "COMPLETED",
            ):
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ConditionalCheckFailedException",
                            "Message": "x",
                        }
                    },
                    "PutItem",
                )
        if cond == "attribute_not_exists(job_id)" and self.existing:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "x",
                    }
                },
                "PutItem",
            )
        self.existing = kwargs["Item"]


def test_try_claim_new_job():
    table = _FakeTable()
    assert (
        try_claim_job(
            table,
            job_id="j1",
            source_link="s3://b/a.wav",
            destination_link="s3://b/t/j1.json",
            language="pt",
        )
        is True
    )
    assert table.existing["status"] == "PENDING"


def test_try_claim_skips_in_progress():
    table = _FakeTable({"job_id": "j1", "status": "IN_PROGRESS"})
    assert (
        try_claim_job(
            table,
            job_id="j1",
            source_link="s",
            destination_link="d",
            language="pt",
        )
        is False
    )
