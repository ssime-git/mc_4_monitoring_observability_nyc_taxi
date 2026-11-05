import os

# Unit tests must not try to export traces to a Tempo that is not running.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
