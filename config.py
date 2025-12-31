class Config:
    HOST_DB: str | None = None
    USER_DB: str | None = None
    PASS_DB: str | None = None
    SSL: str | None = None
    PORT: str | None = None
    DB_NAME: str | None = "mgdb"
    STAFF_DB_NAME: str | None = "erfandb"
    SENDER: str | None = None
    GMAIL_PASS: str | None = None


class Production(Config):
    HOST_DB = "db-mysql-syd1-81835-do-user-27361007-0.f.db.ondigitalocean.com"
    USER_DB = "doadmin"
    PASS_DB = "AVNS_MrBXSYxT8q2BqyNkdmP"
    SSL = "ca-certificate.crt"
    PORT = "25060"
    DB_NAME = "mgdb"
    SENDER = "malaysiangroceries7@gmail.com"
    GMAIL_PASS = "lehejrqmlsrtvwoc"
