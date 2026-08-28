"""Regression guards for the profile-collection hang.

A real voice call sat in silence for eight minutes inside `schedule_appointment`
before the caller gave up. `profile_authenticator` had handed the parent
transcript to its `TaskGroup`, so `GetNameTask` opened with the caller's name
already in context. Its `on_enter` then tells the model to *confirm* the name
rather than collect it -- but `confirm_name` is only registered as a side effect
of `update_name` running. The model called a tool that did not exist, no
completion path fired, and the await never returned.

These checks are cheap and deterministic. What they CANNOT do is prove the hang
is gone: a subtask completes only when the model chooses to call one of its
tools, so the real verification is a live call. They pin the two things that are
checkable -- that our code no longer sets up the trap, and that the framework
assumption behind the workaround still holds.

No LLM, no network, no credentials.

    uv run test_profile_authenticator.py
"""

from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

from livekit.agents.beta.workflows import GetNameTask

FAILURES: list[str] = []


def check(label: str, expected: object, actual: object) -> None:
    if expected == actual:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}\n       expected {expected!r}\n       actual   {actual!r}")
        FAILURES.append(label)


# --------------------------------------------------------------- our own code
# Read agent.py as a syntax tree rather than importing it: importing pulls in
# the whole agent, and all we want to know is how two calls are written.

_TREE = ast.parse(io.open(Path(__file__).parent / "agent.py", encoding="utf-8").read())

_task_group_calls = [
    node
    for node in ast.walk(_TREE)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id == "TaskGroup"
]
check("agent.py builds exactly one TaskGroup", 1, len(_task_group_calls))

# Passing the parent transcript is what put the answers in front of the subtasks.
_kwargs = {kw.arg for kw in _task_group_calls[0].keywords} if _task_group_calls else set()
check("the TaskGroup is built without chat_ctx", False, "chat_ctx" in _kwargs)

# An unbounded await inside a tool call leaves the caller listening to silence
# with no way out. Every await of the task group must be bounded.
_awaits_of_task_group = [
    node
    for node in ast.walk(_TREE)
    if isinstance(node, ast.Await)
    and isinstance(node.value, ast.Name)
    and node.value.id == "task_group"
]
check("no bare `await task_group` remains", 0, len(_awaits_of_task_group))

_wait_for_calls = [
    node
    for node in ast.walk(_TREE)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "wait_for"
    and any(isinstance(a, ast.Name) and a.id == "task_group" for a in node.args)
]
check("the task group is awaited under a timeout", 1, len(_wait_for_calls))


# ------------------------------------------------------- framework assumption
# The workaround exists because of a trap in the library. If a future
# livekit-agents upgrade registers `confirm_name` up front, this check fails --
# which is the signal to revisit whether the workaround is still needed, not a
# sign that anything is broken.

_task = GetNameTask(last_name=True, require_confirmation=True)
_tool_ids = {tool.id for tool in _task.tools}

check(
    "GetNameTask's spoken instructions still point at confirm_name",
    True,
    "confirm_name" in _task.instructions.audio,
)
check(
    "...while confirm_name is still absent from its tools at entry",
    False,
    "confirm_name" in _tool_ids,
)

# The scheduling task is not vulnerable to the confirm-tool trap -- its
# on_enter registers confirm_doctor_selection before the first model turn -- but
# an unbounded await inside a tool call hangs the caller just the same.
_bare_awaits_of_scheduling = [
    node
    for node in ast.walk(_TREE)
    if isinstance(node, ast.Await)
    and isinstance(node.value, ast.Call)
    and isinstance(node.value.func, ast.Name)
    and node.value.func.id == "ScheduleAppointmentTask"
]
check("no bare `await ScheduleAppointmentTask(...)` remains", 0, len(_bare_awaits_of_scheduling))

# Count-independent: every place the scheduling task is constructed for
# awaiting must sit inside a wait_for, however many call sites there are.
_bounded_args = {
    id(arg)
    for node in ast.walk(_TREE)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "wait_for"
    for arg in node.args
}
_scheduling_calls = [
    node
    for node in ast.walk(_TREE)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id == "ScheduleAppointmentTask"
]
check("there is at least one scheduling call site to guard", True, len(_scheduling_calls) > 0)
check(
    "every scheduling call site is awaited under a timeout",
    [],
    [n.lineno for n in _scheduling_calls if id(n) not in _bounded_args],
)

print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("all checks passed")
