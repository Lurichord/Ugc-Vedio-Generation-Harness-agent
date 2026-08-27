from __future__ import annotations

from uuid import uuid4

from ugc_harness.harness.models import ProjectState

from .repository import AppDataRepository
from .schemas import ApprovalRecord, FeedbackRecord, FeedbackRequest, StageName


class ReviewService:
    def __init__(self, repository: AppDataRepository) -> None:
        self.repository = repository

    def valid_approval(
        self, project_id: str, stage: StageName, state: ProjectState
    ) -> ApprovalRecord | None:
        matches = [
            item
            for item in self.repository.approvals(project_id)
            if item.stage == stage
        ]
        if not matches:
            return None
        approval = matches[-1]
        for ref, approved_version in approval.approved_refs.items():
            node = state.dependency_graph.nodes.get(ref)
            if node is None or node.status != "current" or node.version != approved_version:
                return None
        return approval

    def open_feedback(
        self, project_id: str, stage: StageName | None = None
    ) -> list[FeedbackRecord]:
        return [
            item
            for item in self.repository.feedback(project_id)
            if item.status in {"open", "repairing"}
            and (stage is None or item.stage == stage)
        ]

    def approve(
        self,
        project_id: str,
        stage: StageName,
        state: ProjectState,
        beat_ids: list[str],
        refs: list[str],
    ) -> ApprovalRecord:
        approved_refs = {
            ref: state.dependency_graph.nodes[ref].version
            for ref in refs
            if ref in state.dependency_graph.nodes
            and state.dependency_graph.nodes[ref].status == "current"
        }
        record = ApprovalRecord(
            approval_id=f"approval_{uuid4().hex}",
            stage=stage,
            approved_refs=approved_refs,
            beat_ids=sorted(set(beat_ids)),
            state_version=state.video.state_version,
        )
        self.repository.save_approval(project_id, record)
        return record

    def add_feedback(
        self, project_id: str, state: ProjectState, request: FeedbackRequest
    ) -> FeedbackRecord:
        if request.expected_state_version != state.video.state_version:
            raise ValueError("STALE_RESULT: project state changed; refresh and retry")
        node = state.dependency_graph.nodes.get(request.target_ref)
        if node is None:
            raise ValueError("unknown dependency target")
        if node.version != request.expected_node_version:
            raise ValueError("STALE_RESULT: target node changed; refresh and retry")
        record = FeedbackRecord(
            feedback_id=f"feedback_{uuid4().hex}",
            stage=request.stage,
            beat_id=request.beat_id,
            target_ref=request.target_ref,
            node_version=node.version,
            instruction=request.instruction,
        )
        self.repository.save_feedback(project_id, record)
        return record

