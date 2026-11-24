from agent_runtime.benchmark.runner import BenchmarkTask, grade_text, summarize


def test_grader_and_summary():
    task = BenchmarkTask("x", "do thing", ["alpha", "beta"], ["smoke"])
    grade = grade_text(task, "alpha only")
    assert grade.score == 0.5
    assert not grade.passed
    assert summarize([grade])["success_rate"] == 0.0
