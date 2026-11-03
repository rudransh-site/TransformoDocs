class PipelineError(Exception):
    """A processing failure with a friendly message for the user."""

    def __init__(self, code, user_message, retryable=False, notify_admin=False, severity="WARNING"):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        self.notify_admin = notify_admin
        self.severity = severity
