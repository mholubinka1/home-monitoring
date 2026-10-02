from hive_app.data.notify import ReauthAlert


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def notify_reauth_required(self) -> None:
        self.calls.append("required")

    def notify_auth_recovered(self) -> None:
        self.calls.append("recovered")


class _NotifierWhoseRequiredDeliveryFails(_RecordingNotifier):
    def notify_reauth_required(self) -> None:
        raise ConnectionError("ntfy.sh unreachable")


def test_clearing_when_no_required_alert_was_ever_raised_sends_nothing() -> None:
    notifier = _RecordingNotifier()

    ReauthAlert(notifier).clear()

    assert not notifier.calls


def test_clearing_after_a_failed_required_delivery_sends_nothing() -> None:
    notifier = _NotifierWhoseRequiredDeliveryFails()
    alert = ReauthAlert(notifier)
    alert.notify_once()

    alert.clear()

    assert not notifier.calls


class _NotifierWhoseRecoveredDeliveryFails(_RecordingNotifier):
    def notify_auth_recovered(self) -> None:
        super().notify_auth_recovered()
        raise ConnectionError("ntfy.sh unreachable")


def test_a_failed_recovered_delivery_is_swallowed_and_not_retried() -> None:
    notifier = _NotifierWhoseRecoveredDeliveryFails()
    alert = ReauthAlert(notifier)
    alert.notify_once()

    alert.clear()
    alert.clear()

    assert notifier.calls == ["required", "recovered"]


def test_an_alert_without_a_notifier_does_nothing_when_raised_or_cleared() -> None:
    alert = ReauthAlert(None)

    alert.notify_once()
    alert.clear()


def test_clearing_after_a_delivered_required_alert_sends_one_recovered_notice() -> None:
    notifier = _RecordingNotifier()
    alert = ReauthAlert(notifier)
    alert.notify_once()

    alert.clear()
    alert.clear()

    assert notifier.calls == ["required", "recovered"]
