import logging

from docguard.logging_config import ExcludeTaskListAccessLogFilter


def access_record(method: str, path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:12345", method, path, "1.1", 200),
        None,
    )


def test_task_list_get_access_log_is_suppressed():
    log_filter = ExcludeTaskListAccessLogFilter()

    assert not log_filter.filter(access_record("GET", "/api/v1/tasks"))
    assert not log_filter.filter(access_record("GET", "/api/v1/tasks?limit=20"))


def test_other_access_logs_are_preserved():
    log_filter = ExcludeTaskListAccessLogFilter()

    assert log_filter.filter(access_record("POST", "/api/v1/tasks"))
    assert log_filter.filter(access_record("GET", "/api/v1/tasks/example-task"))
