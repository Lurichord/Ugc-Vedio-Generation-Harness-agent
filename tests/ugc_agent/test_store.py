from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from ugc_agent.state import MediaAsset, Outline, ProjectState, Story
from ugc_agent.store import ProjectStore, RevisionConflict
from ugc_agent.tools.state import _save


def _outline(topic: str = "手冲咖啡") -> Outline:
    return Outline(
        topic=topic,
        video_type="explainer",
        audience="咖啡初学者",
        visual_form="桌面演示",
    )


def _runtime(store: ProjectStore, project_id: str = "demo"):
    return SimpleNamespace(
        context=SimpleNamespace(project_store=store, project_id=project_id)
    )


def test_project_store_saves_atomically_and_rejects_stale_revision(tmp_path):
    store = ProjectStore(tmp_path)
    state = ProjectState(project_id="demo", outline=_outline())

    saved = store.save(state)

    assert saved.revision == 1
    assert store.load("demo") == saved
    created = store.create("other")
    assert created.revision == 0
    assert store.exists("demo")
    assert store.list_ids() == ["demo", "other"]
    again = store.save(saved)
    assert again.revision == 2
    with pytest.raises(RevisionConflict, match="expected revision 0, got 2"):
        store.save(state)


def test_save_tools_retry_parallel_layer_updates(tmp_path):
    store = ProjectStore(tmp_path)
    store.create("demo")
    runtime = _runtime(store)

    def write_outline():
        return _save(runtime, lambda state: state.model_copy(update={"outline": _outline()}))

    def write_story():
        return _save(
            runtime,
            lambda state: state.model_copy(update={"story": Story(text="口播稿")}),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write_outline)
        second = pool.submit(write_story)
        first.result()
        second.result()

    loaded = store.load("demo")
    assert loaded.outline.topic == "手冲咖啡"
    assert loaded.story.text == "口播稿"
    assert loaded.revision == 2


def test_save_tools_retry_parallel_record_media(tmp_path):
    store = ProjectStore(tmp_path)
    store.create("demo")
    runtime = _runtime(store)

    def record(media_id: str):
        asset = MediaAsset(
            media_id=media_id,
            kind="image",
            path=f"{media_id}.png",
            owner_ref="project",
            source="generated",
        )

        def mutate(state: ProjectState) -> ProjectState:
            updated = state.model_copy(deep=True)
            updated.media[asset.media_id] = asset
            return updated

        return _save(runtime, mutate)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(record, f"img_{i}") for i in range(8)]
        for fut in futs:
            fut.result()

    loaded = store.load("demo")
    assert set(loaded.media) == {f"img_{i}" for i in range(8)}
    assert loaded.revision == 8
