import os

from dotenv import load_dotenv
from flask import Flask

from app.db import init_pool

load_dotenv()


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DATABASE_URL"] = os.getenv("DATABASE_URL", "")
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")
    app.config["DB_POOL_MIN"] = int(os.getenv("DB_POOL_MIN", "2"))
    app.config["DB_POOL_MAX"] = int(os.getenv("DB_POOL_MAX", "10"))

    if config:
        app.config.update(config)

    init_pool(
        app.config["DATABASE_URL"],
        min_size=app.config["DB_POOL_MIN"],
        max_size=app.config["DB_POOL_MAX"],
    )

    @app.teardown_appcontext
    def shutdown(_: BaseException | None) -> None:
        pass  # pool is closed on app close below

    from app.routes.projects import bp as projects_bp
    from app.routes.test_runs import bp as runs_bp
    from app.routes.tests import bp as tests_bp
    from app.routes.transactions import bp as transactions_bp

    app.register_blueprint(projects_bp,     url_prefix="/api/projects")
    app.register_blueprint(tests_bp,        url_prefix="/api/tests")
    app.register_blueprint(runs_bp,         url_prefix="/api/runs")
    app.register_blueprint(transactions_bp, url_prefix="/api/transactions")

    return app
