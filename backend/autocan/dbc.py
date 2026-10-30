"""Load the packaged, original synthetic DemoCar profile."""

from importlib.resources import files

from cantools.database.can import Database

PROFILES = {
    "democar": ("democar-0.1.0", "democar.dbc"),
    "democar-network": ("democar-0.2.0", "democar_network.dbc"),
}


def profile_version(profile_id: str) -> str:
    if profile_id not in PROFILES:
        raise ValueError("unsupported DemoCar profile")
    return PROFILES[profile_id][0]


def profile_from_version(version: object) -> str:
    for name, (supported, _) in PROFILES.items():
        if version == supported:
            return name
    raise ValueError("unsupported profile version")


def profile_bytes(profile_id: str = "democar") -> bytes:
    profile_version(profile_id)
    return files("autocan").joinpath("profiles", PROFILES[profile_id][1]).read_bytes()


def load_democar_database(profile_id: str = "democar") -> Database:
    """Return a fresh, strictly validated CAN database, independent of the cwd."""
    text = profile_bytes(profile_id).decode("utf-8")
    database = Database(strict=True)
    database.add_dbc_string(text)
    return database
