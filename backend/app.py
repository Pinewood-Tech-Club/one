"""
Pinewood One Backend - Main Application Entry Point
"""
import os

from flask import Flask, jsonify
from flask_cors import CORS

from api.routes import api_bp

# Import blueprints
from auth.routes import auth_bp
from chat.routes import chat_api_bp
from config import Config
from db.init import init_db
from events.routes import events_bp
from extensions import limiter
from mobile.routes import mobile_bp
from schoology.routes import oauth_bp as schoology_oauth_bp
from schoology.routes import schoology_api_bp
from services.chat.reaper import start_reaper


def create_app():
    """Application factory"""
    app = Flask(__name__)
    
    # Load configuration
    app.secret_key = Config.SECRET_KEY
    # Only require HTTPS cookies in production (allows cookies on localhost)
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = Config.SESSION_LIFETIME
    
    # CORS configuration
    CORS(app, origins=[Config.FRONTEND_URL, "http://localhost:3112"], supports_credentials=True)

    # Rate limiter
    app.config["RATELIMIT_STORAGE_URI"] = Config.RATELIMIT_STORAGE_URI
    limiter.init_app(app)
    
    # Validate configuration
    Config.validate()
    
    # Initialize databases
    init_db()
    
    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(chat_api_bp)
    app.register_blueprint(schoology_oauth_bp)
    app.register_blueprint(schoology_api_bp)
    app.register_blueprint(mobile_bp)

    # Stale-generation reaper (single process, use_reloader=False → starts once)
    start_reaper()

    @app.errorhandler(429)
    def handle_rate_limit(_error):
        return jsonify({"error": "rate_limited"}), 429
    
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(
        debug=True,
        threaded=True,
        use_reloader=False,
        port=3111,
        host="0.0.0.0",
    )
