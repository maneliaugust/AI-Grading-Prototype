from moodle_client import _call

result = _call(
    "local_aigrader_set_essay_grade",
    {
        "attemptid": 4,      # replace with a real attempt id
        "slot": 11,           # replace with the essay question slot
        "grade": 5,
        "feedback": "Good answer",
        "feedbackformat": 1,
    },
    method="POST",
)

print(result)