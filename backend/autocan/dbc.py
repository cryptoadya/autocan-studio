"""Load the packaged, original synthetic DemoCar profile."""

from importlib.resources import files

from cantools.database.can import Database


def load_democar_database() -> Database:
    """Return a fresh, strictly validated CAN database, independent of the cwd."""
    text = files("autocan").joinpath("profiles/democar.dbc").read_text(encoding="utf-8")
    database = Database(strict=True)
    database.add_dbc_string(text)
    return database
