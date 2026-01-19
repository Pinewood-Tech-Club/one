"""
Configuration module for Pinewood One Backend
"""
import os
import secrets
from datetime import timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Config:
    """Application configuration"""
    
    # Flask configuration
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))
    SESSION_LIFETIME = timedelta(days=7)
    
    # URLs
    FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3112")
    BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:3111")
    
    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "your-client-id")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "your-client-secret")
    
    # Schoology OAuth
    SCHOOLOGY_CONSUMER_KEY = os.environ.get("SCHOOLOGY_CONSUMER_KEY")
    SCHOOLOGY_CONSUMER_SECRET = os.environ.get("SCHOOLOGY_CONSUMER_SECRET")
    SCHOOLOGY_DOMAIN = os.environ.get("SCHOOLOGY_DOMAIN", "https://app.schoology.com")
    SCHOOLOGY_API_DOMAIN = os.environ.get("SCHOOLOGY_API_DOMAIN", "https://api.schoology.com")
    
    # Encryption
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")
    
    # Database paths
    MAIN_DB_PATH = "main.db"
    SESSIONS_DB_PATH = "api_sessions.db"

    # Convex configuration
    CONVEX_URL = os.environ.get("CONVEX_URL", "https://hearty-lemur-131.convex.cloud")

    # Schoology Service
    SCHOOLOGY_SERVICE_KEY = os.environ.get("SCHOOLOGY_SERVICE_KEY")
    SCHOOLOGY_SERVICE_URL = os.environ.get("SCHOOLOGY_SERVICE_URL", "http://localhost:3113")

    @classmethod
    def validate(cls):
        """Validate configuration and print status"""
        if not cls.SCHOOLOGY_CONSUMER_KEY or not cls.SCHOOLOGY_CONSUMER_SECRET:
            print("⚠️  WARNING: Schoology OAuth credentials not found in environment variables!")
            print("   Please set SCHOOLOGY_CONSUMER_KEY and SCHOOLOGY_CONSUMER_SECRET in .env")
        else:
            print(f"✅ Schoology OAuth configured with official developer credentials")
            print(f"   Consumer Key: {cls.SCHOOLOGY_CONSUMER_KEY[:20]}...")
            print(f"   Domain: {cls.SCHOOLOGY_DOMAIN}")

