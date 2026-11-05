import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# to check db connection, tables, etc.

url = URL.create(
    "mysql+pymysql",
    username=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    host=os.environ["DB_HOST"],
    port=int(os.environ["DB_PORT"]),
    database=os.environ["DB_NAME"],
)
engine = create_engine(
    url,
    connect_args={
        "ssl_ca": os.path.join(BASE_DIR, os.environ["DB_SSL_CA"]),
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    },
)

with engine.connect() as conn:
    print("User:  ", conn.execute(text("SELECT CURRENT_USER()")).scalar())
    print("Tables:", [row[0] for row in conn.execute(text("SHOW TABLES"))])
    print(
        "SSL:   ",
        conn.execute(text("SHOW SESSION STATUS LIKE 'Ssl_cipher'")).fetchone(),
    )

insp = inspect(engine)
for table in insp.get_table_names():
    print(f"\n=== {table} ===")
    for col in insp.get_columns(table):
        print(f"  {col['name']:<22} {str(col['type']):<20} "
              f"nullable={col['nullable']}  default={col['default']}  "
              f"autoinc={col.get('autoincrement')}")
    print("  PK:", insp.get_pk_constraint(table)["constrained_columns"])
    for fk in insp.get_foreign_keys(table):
        print("  FK:", fk["constrained_columns"], "->",
              fk["referred_table"], fk["referred_columns"])
    for ix in insp.get_indexes(table):
        print("  INDEX:", ix["name"], ix["column_names"], "unique=" + str(ix["unique"]))
