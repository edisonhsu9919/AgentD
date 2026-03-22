"""Sample code file for testing file_read and code review skill."""

import os


def process_data(items):
    result = []
    for item in items:
        if item > 0:
            result.append(item * 2)
    return result


def read_config(path):
    # WARNING: no input validation on path
    with open(path) as f:
        return f.read()


def connect_db(host, port, password):
    # BUG: password logged in plaintext
    print(f"Connecting to {host}:{port} with password={password}")
    return {"host": host, "port": port}


if __name__ == "__main__":
    data = process_data([1, -2, 3, 0, 5])
    print(data)
