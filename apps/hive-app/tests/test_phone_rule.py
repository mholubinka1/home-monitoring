import pytest

from hive_app.data.phone_rule import PhoneRule

RANDOMISED_MAC = "c2:00:00:00:00:01"
FACTORY_MAC = "c0:00:00:00:00:01"

PHONES = [
    "iPhone", "Alexs-iPhone", "Alex-s-iPhone.lan", "iPhone-15-Pro", "Pixel-8a",
    "Pixel-9-Pro-XL", "Galaxy-S24-Ultra", "Galaxy-Z-Flip6", "Galaxy-A55-5G",
    "SM-S921B", "Redmi-Note-13-Pro", "POCO-F6", "Xiaomi-14T", "OnePlus-12",
    "Nord-CE4", "moto-g85-5G", "motorola-razr-50", "Xperia-1-VI", "Nokia-G42-5G",
    "realme-GT-6", "OPPO-Reno12", "vivo-X100-Pro", "HONOR-Magic6-Pro",
    "HUAWEI-P60", "Nothing-Phone-2a", "Fairphone-5", "Zenfone-11-Ultra",
    "ROG-Phone-8", "android-1a2b3c4d5e6f7a8b", "Alexs-Phone", "Alex-Mobile",
]  # fmt: skip

NOT_PHONES = [
    "Galaxy-Tab-S9", "Alex-s-Tab-S8", "iPad", "Pixel-Tablet", "Xiaomi-Pad-6",
    "Lenovo-Tab-P12", "HUAWEI-MatePad-11", "OnePlus-Pad", "SM-X710",
    "Pixel-Watch-3", "Galaxy-Watch6", "Alexs-MacBook-Air", "Galaxy-Book4",
    "DESKTOP-ABC1234", "Samsung-TV", "QZXW123ABC", "Kitchen", "", "*", "localhost",
]  # fmt: skip

RULE = PhoneRule(phone_hostnames=[], ignored_hostnames=[])


@pytest.mark.parametrize("hostname", PHONES)
def test_a_randomised_mac_device_with_a_phone_hostname_is_a_phone(
    hostname: str,
) -> None:
    assert RULE.is_phone(RANDOMISED_MAC, [hostname])


@pytest.mark.parametrize("hostname", NOT_PHONES)
def test_a_randomised_mac_device_with_a_tablet_watch_computer_media_or_unknown_name_is_not_a_phone(
    hostname: str,
) -> None:
    assert not RULE.is_phone(RANDOMISED_MAC, [hostname])


def test_a_device_without_a_hostname_is_not_a_phone() -> None:
    assert not RULE.is_phone(RANDOMISED_MAC, [])


@pytest.mark.parametrize(
    ("hwaddr", "randomised"),
    [
        ("02:00:00:00:00:00", True),
        ("06:00:00:00:00:00", True),
        ("8a:00:00:00:00:00", True),
        ("DE:00:00:00:00:00", True),
        ("00:00:00:00:00:00", False),
        ("dc:a6:32:00:00:00", False),
        ("03:00:00:00:00:00", False),
        ("ip-192.0.2.5", False),
        ("", False),
    ],
)
def test_only_a_locally_administered_unicast_mac_passes_the_gate_and_nothing_raises(
    hwaddr: str, randomised: bool
) -> None:
    assert RULE.is_phone(hwaddr, ["Pixel-8"]) is randomised


def test_a_configured_phone_hostname_bypasses_the_mac_gate_and_word_rules() -> None:
    rule = PhoneRule(phone_hostnames=["kevin", "Galaxy-Tab-S9"], ignored_hostnames=[])

    assert rule.is_phone(FACTORY_MAC, ["Kevin.lan"])
    assert rule.is_phone("ip-192.0.2.5", ["KEVIN"])
    assert rule.is_phone(RANDOMISED_MAC, ["galaxy-tab-s9"])


def test_an_ignored_hostname_wins_over_everything_including_a_configured_phone() -> (
    None
):
    rule = PhoneRule(phone_hostnames=["Pixel-8"], ignored_hostnames=["pixel-8"])

    assert not rule.is_phone(RANDOMISED_MAC, ["Pixel-8"])


def test_a_device_is_a_phone_if_any_of_its_hostnames_is() -> None:
    assert RULE.is_phone(RANDOMISED_MAC, ["Kitchen", "Alex-s-Phone"])


def test_the_same_input_gives_the_same_answer_every_time() -> None:
    answers = {RULE.is_phone(RANDOMISED_MAC, ["Pixel-8.lan"]) for _ in range(100)}

    assert answers == {True}
