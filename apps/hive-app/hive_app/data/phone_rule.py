import re
from collections.abc import Iterable

RANDOMISED_MAC_PATTERN = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.IGNORECASE)
LOCALLY_ADMINISTERED_BIT = 0x02
MULTICAST_BIT = 0x01

UNUSABLE_HOSTNAMES = frozenset({"", "*", "localhost"})
WORD_PATTERN = re.compile(r"[a-z]+|\d+")
LEGACY_ANDROID_PATTERN = re.compile(r"^android-[0-9a-f]{8,16}$", re.IGNORECASE)
SAMSUNG_PHONE_MODEL_PATTERN = re.compile(r"(?:^|-)sm-[asgfnme]\d{3}", re.IGNORECASE)

NOT_PHONE_WORD_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "tab",
            "tablet",
            "ipad",
            "pad",
            "matepad",
            "mediapad",
            "kindle",
            "fire",
            "lenovotab",
            "galaxytab",
        }
    ),
    frozenset({"watch", "band", "fit", "gear", "wear"}),
    frozenset(
        {
            "macbook",
            "imac",
            "mac",
            "mbp",
            "mba",
            "laptop",
            "desktop",
            "pc",
            "chromebook",
            "pixelbook",
            "book",
            "surface",
            "thinkpad",
            "windows",
            "ubuntu",
            "linux",
            "raspberrypi",
        }
    ),
    frozenset(
        {
            "tv",
            "chromecast",
            "roku",
            "firetv",
            "appletv",
            "shield",
            "xbox",
            "playstation",
            "ps4",
            "ps5",
            "nintendo",
            "switch",
            "echo",
            "sonos",
        }
    ),
)
PHONE_WORDS = frozenset(
    {
        "iphone",
        "phone",
        "smartphone",
        "mobile",
        "android",
        "pixel",
        "galaxy",
        "samsung",
        "xiaomi",
        "redmi",
        "poco",
        "oneplus",
        "nord",
        "motorola",
        "moto",
        "razr",
        "xperia",
        "nokia",
        "hmd",
        "realme",
        "oppo",
        "vivo",
        "iqoo",
        "honor",
        "huawei",
        "fairphone",
        "zenfone",
        "rog",
        "asus",
        "tcl",
        "alcatel",
        "zte",
        "blade",
        "nubia",
        "redmagic",
        "infinix",
        "tecno",
        "itel",
        "doogee",
        "ulefone",
        "oukitel",
        "blackview",
        "cat",
        "lg",
        "htc",
        "meizu",
        "lenovo",
        "sharp",
        "aquos",
        "kyocera",
    }
)


def _label(hostname: str) -> str:
    return hostname.split(".", 1)[0].strip().lower()


def _is_randomised_mac(hwaddr: str) -> bool:
    if not RANDOMISED_MAC_PATTERN.match(hwaddr):
        return False
    first_octet = int(hwaddr[:2], 16)
    return bool(first_octet & LOCALLY_ADMINISTERED_BIT) and not (
        first_octet & MULTICAST_BIT
    )


def _names_a_phone(label: str) -> bool:
    words = set(WORD_PATTERN.findall(label))
    if any(words & group for group in NOT_PHONE_WORD_GROUPS):
        return False
    return bool(
        LEGACY_ANDROID_PATTERN.match(label)
        or SAMSUNG_PHONE_MODEL_PATTERN.search(label)
        or words & PHONE_WORDS
    )


class PhoneRule:
    """Decides whether a Pi-hole device is a Phone."""

    def __init__(
        self, phone_hostnames: Iterable[str], ignored_hostnames: Iterable[str]
    ) -> None:
        self._phone_hostnames = {_label(name) for name in phone_hostnames}
        self._ignored_hostnames = {_label(name) for name in ignored_hostnames}

    def is_phone(self, hwaddr: str, hostnames: Iterable[str]) -> bool:
        return any(self._hostname_is_phone(hwaddr, name) for name in hostnames)

    def _hostname_is_phone(self, hwaddr: str, hostname: str) -> bool:
        label = _label(hostname)
        if label in self._ignored_hostnames:
            return False
        if label in self._phone_hostnames:
            return True
        if not _is_randomised_mac(hwaddr) or label in UNUSABLE_HOSTNAMES:
            return False
        return _names_a_phone(label)
