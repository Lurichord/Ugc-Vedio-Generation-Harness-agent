from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .models import (
    DependencyGraphState,
    DependencyNode,
    DependencySnapshot,
    GraphUpdateRecord,
)


def semantic_hash(value: BaseModel | dict[str, Any] | list[Any] | str) -> str:
    if isinstance(value, BaseModel):
        payload: Any = value.model_dump(mode="json", exclude={"generated_at"})
    else:
        payload = value
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class NodeCommit:
    ref: str
    kind: str
    value: BaseModel | dict[str, Any] | list[Any] | str
    depends_on: tuple[str, ...] = ()


class DependencyGraph:
    """Mutable service around the persisted, bidirectional artifact DAG."""

    def __init__(self, state: DependencyGraphState) -> None:
        self.state = state

    def snapshot(self, refs: list[str]) -> list[DependencySnapshot]:
        result: list[DependencySnapshot] = []
        for ref in refs:
            node = self.state.nodes.get(ref)
            if node is None or node.status != "current":
                raise ValueError(f"dependency is not current: {ref}")
            result.append(
                DependencySnapshot(
                    ref=ref,
                    version=node.version,
                    content_hash=node.content_hash,
                )
            )
        return result

    def validate_snapshot(self, snapshots: list[DependencySnapshot]) -> None:
        for snapshot in snapshots:
            node = self.state.nodes.get(snapshot.ref)
            if (
                node is None
                or node.status != "current"
                or node.version != snapshot.version
                or node.content_hash != snapshot.content_hash
            ):
                raise ValueError(
                    f"STALE_RESULT: dependency changed: {snapshot.ref}"
                )

    def dependencies_satisfied(self, refs: list[str]) -> bool:
        return all(
            (node := self.state.nodes.get(ref)) is not None
            and node.status == "current"
            for ref in refs
        )

    def commit_batch(
        self,
        *,
        task_id: str,
        produced_by: str,
        commits: list[NodeCommit],
    ) -> GraphUpdateRecord:
        working_state = self.state.model_copy(deep=True)
        update = DependencyGraph(working_state)._commit_batch_in_place(
            task_id=task_id,
            produced_by=produced_by,
            commits=commits,
        )
        self.state.graph_version = working_state.graph_version
        self.state.nodes = working_state.nodes
        return update

    def _commit_batch_in_place(
        self,
        *,
        task_id: str,
        produced_by: str,
        commits: list[NodeCommit],
    ) -> GraphUpdateRecord:
        before = self.state.graph_version
        commit_refs = {item.ref for item in commits}
        if len(commit_refs) != len(commits):
            raise ValueError("dependency commit refs must be unique")
        available = set(self.state.nodes) | commit_refs
        for item in commits:
            missing = set(item.depends_on) - available
            if missing:
                raise ValueError(
                    f"dependency node {item.ref} has missing inputs: {sorted(missing)}"
                )

        changed: set[str] = set()
        refreshed: set[str] = set()
        for item in commits:
            content_hash = semantic_hash(item.value)
            previous = self.state.nodes.get(item.ref)
            content_changed = previous is None or previous.content_hash != content_hash
            if previous and previous.locked and content_changed:
                raise ValueError(f"dependency node is locked: {item.ref}")
            if content_changed:
                changed.add(item.ref)
            else:
                refreshed.add(item.ref)

            old_parents = set(previous.depends_on) if previous else set()
            new_parents = set(item.depends_on)
            for parent_ref in old_parents - new_parents:
                parent = self.state.nodes.get(parent_ref)
                if parent:
                    parent.dependents = sorted(
                        set(parent.dependents) - {item.ref}
                    )

            dependencies = [
                self.state.nodes[ref]
                for ref in item.depends_on
                if ref in self.state.nodes
            ]
            version = 1 if previous is None else previous.version + int(content_changed)
            node = DependencyNode(
                ref=item.ref,
                kind=item.kind,
                version=version,
                content_hash=content_hash,
                depends_on=sorted(new_parents),
                dependents=sorted(previous.dependents) if previous else [],
                dependency_versions={dep.ref: dep.version for dep in dependencies},
                dependency_hashes={dep.ref: dep.content_hash for dep in dependencies},
                status="current",
                produced_by=produced_by,
                last_task_id=task_id,
                locked=previous.locked if previous else False,
            )
            self.state.nodes[item.ref] = node
            for parent_ref in new_parents:
                parent = self.state.nodes.get(parent_ref)
                if parent:
                    parent.dependents = sorted(set(parent.dependents) | {item.ref})

        # Some dependencies can be committed later in the same atomic batch.
        for item in commits:
            node = self.state.nodes[item.ref]
            node.dependency_versions = {
                ref: self.state.nodes[ref].version for ref in node.depends_on
            }
            node.dependency_hashes = {
                ref: self.state.nodes[ref].content_hash for ref in node.depends_on
            }
            for parent_ref in node.depends_on:
                parent = self.state.nodes[parent_ref]
                parent.dependents = sorted(set(parent.dependents) | {node.ref})

        self._validate_acyclic()
        invalidated = self._invalidate_descendants(changed, exclude=commit_refs)
        self.validate_integrity()
        self.state.graph_version += 1
        return GraphUpdateRecord(
            update_id=f"graph_update_{task_id}_{self.state.graph_version}",
            task_id=task_id,
            graph_version_before=before,
            graph_version_after=self.state.graph_version,
            committed=True,
            changed_refs=sorted(changed),
            refreshed_refs=sorted(refreshed),
            invalidated_refs=sorted(invalidated),
            reason="critic approved task outputs and dependency graph was committed",
        )

    def reject_update(
        self,
        *,
        task_id: str,
        candidate_refs: list[str],
        reason: str,
    ) -> GraphUpdateRecord:
        before = self.state.graph_version
        self.state.graph_version += 1
        return GraphUpdateRecord(
            update_id=f"graph_update_{task_id}_{self.state.graph_version}",
            task_id=task_id,
            graph_version_before=before,
            graph_version_after=self.state.graph_version,
            committed=False,
            rejected_refs=sorted(set(candidate_refs)),
            reason=reason,
        )

    def _invalidate_descendants(
        self,
        roots: set[str],
        *,
        exclude: set[str],
    ) -> set[str]:
        invalidated: set[str] = set()
        queue = [
            ref
            for root in roots
            for ref in self.state.nodes[root].dependents
        ]
        while queue:
            ref = queue.pop(0)
            if ref in invalidated or ref in exclude:
                continue
            node = self.state.nodes[ref]
            node.status = "stale"
            invalidated.add(ref)
            queue.extend(node.dependents)
        return invalidated

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(ref: str) -> None:
            if ref in visiting:
                raise ValueError(f"dependency cycle detected at {ref}")
            if ref in visited:
                return
            visiting.add(ref)
            for dependency in self.state.nodes[ref].depends_on:
                visit(dependency)
            visiting.remove(ref)
            visited.add(ref)

        for ref in self.state.nodes:
            visit(ref)

    def validate_integrity(self) -> None:
        for key, node in self.state.nodes.items():
            if key != node.ref:
                raise ValueError(f"dependency node key/ref mismatch: {key}")
            if len(node.depends_on) != len(set(node.depends_on)):
                raise ValueError(f"duplicate dependencies on node: {node.ref}")
            if len(node.dependents) != len(set(node.dependents)):
                raise ValueError(f"duplicate dependents on node: {node.ref}")
            if set(node.dependency_versions) != set(node.depends_on):
                raise ValueError(f"dependency version snapshot mismatch: {node.ref}")
            if set(node.dependency_hashes) != set(node.depends_on):
                raise ValueError(f"dependency hash snapshot mismatch: {node.ref}")
            for parent_ref in node.depends_on:
                parent = self.state.nodes.get(parent_ref)
                if parent is None or node.ref not in parent.dependents:
                    raise ValueError(
                        f"missing reverse dependency edge: {parent_ref} -> {node.ref}"
                    )
                if node.status == "current":
                    if parent.status != "current":
                        raise ValueError(
                            f"current node depends on stale node: {node.ref}"
                        )
                    if (
                        node.dependency_versions[parent_ref] != parent.version
                        or node.dependency_hashes[parent_ref] != parent.content_hash
                    ):
                        raise ValueError(
                            f"current dependency snapshot is stale: {node.ref}"
                        )
            for child_ref in node.dependents:
                child = self.state.nodes.get(child_ref)
                if child is None or node.ref not in child.depends_on:
                    raise ValueError(
                        f"missing forward dependency edge: {node.ref} -> {child_ref}"
                    )
