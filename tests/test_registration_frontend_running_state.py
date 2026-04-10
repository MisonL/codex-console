from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_app_js_transitions_submit_button_to_running_state_after_single_start():
    content = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "function transitionStartButtonToRunning()" in content
    assert "currentTask = data;" in content
    assert "transitionStartButtonToRunning();" in content


def test_app_js_transitions_submit_button_to_running_state_after_batch_start():
    content = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "currentBatch = { ...data, pollingMode:" in content
    assert '"batch"' in content
    assert content.count("transitionStartButtonToRunning();") >= 2
