from common.mariadb.model import SQLBase


def test_no_table_is_pinned_to_a_schema_so_all_follow_the_connection_database() -> None:
    from hive_app.data.mysql.model import SQLBase as HiveBase
    from octopus_app.data.mysql.client import weather_metadata
    from octopus_app.data.mysql.model import SQLBase as OctopusBase

    tables = [
        *SQLBase.metadata.tables.values(),
        *HiveBase.metadata.tables.values(),
        *OctopusBase.metadata.tables.values(),
        *weather_metadata.tables.values(),
    ]

    assert tables
    assert [table.fullname for table in tables if table.schema is not None] == []
