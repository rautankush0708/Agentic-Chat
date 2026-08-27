from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from flask import Flask  # noqa: E402  (must import after load_dotenv)
from flask_cors import CORS  # noqa: E402

from .config import Config  # noqa: E402  (reads env vars at import time)
from .extensions import db  # noqa: E402


def create_app(config_object: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    instance_dir = Path(app.root_path).parent / "instance"
    instance_dir.mkdir(exist_ok=True)

    db.init_app(app)
    CORS(app, origins=app.config["CORS_ORIGINS"], supports_credentials=True)

    from .routes.health import health_bp
    from .routes.agent import agent_bp
    from .routes.tts import tts_bp

    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(agent_bp, url_prefix="/api")
    app.register_blueprint(tts_bp, url_prefix="/api")

    with app.app_context():
        db.create_all()

    return app
