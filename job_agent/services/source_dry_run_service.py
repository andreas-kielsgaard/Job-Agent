from __future__ import annotations

from job_agent.services.source_test_service import (
    SourceTestJobPreview,
    SourceTestProgressCallback,
    SourceTestResult,
    SourceTestService,
)

DryRunJobPreview = SourceTestJobPreview
DryRunProgressCallback = SourceTestProgressCallback
SourceDryRunResult = SourceTestResult


class SourceDryRunService(SourceTestService):
    pass
