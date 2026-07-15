import re
from pathlib import Path

from app.main import create_app


M5_OPERATIONS = {
    "activateModelVersion", "cancelAgentRun", "cancelTrainingJob",
    "compareEvaluations", "createAgentRun", "createDataset",
    "createDatasetVersion", "createEvaluation", "createTrainingJob",
    "deactivateModelVersion", "decideAgentToolApproval", "deleteDataset",
    "freezeDatasetVersion", "getAgentRun", "getDataset", "getEvaluation",
    "getModelVersion", "getTool", "getTrainingJob", "invokeInternalTool",
    "listAgentRuns", "listAgents", "listDatasets", "listEvaluations",
    "listModelVersions", "listTools", "listTrainingJobs",
    "registerModelVersion", "streamAgentRun", "updateToolRuntimeState",
    "uploadDatasetArtifact",
}


def test_all_m5_openapi_operations_are_unique_and_implemented():
    source = (Path(__file__).parents[2] / "docx" / "deliverables" / "openapi.yaml").read_text(encoding="utf-8")
    declared = re.findall(r"^\s+operationId:\s*([A-Za-z0-9_]+)\s*$", source, flags=re.MULTILINE)
    assert len(declared) == len(set(declared))
    assert M5_OPERATIONS <= set(declared)
    implemented = {
        route.operation_id
        for route in create_app().routes
        if getattr(route, "operation_id", None)
    }
    assert M5_OPERATIONS <= implemented
