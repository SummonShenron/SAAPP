import os
from pymongo import MongoClient
from dotenv import load_dotenv
import logging
# Load your .env file
load_dotenv()
logger = logging.getLogger("SASS Logger")
# Global variable to hold the client so we don't reconnect every time
_client = None

def get_db():
    """
    Returns the database object if USE_DB is true, else returns None.
    """
    global _client
    
    if os.getenv("USE_DB") != "true":
        logger.info("Using MongoDB")
        return None
        
    if _client is None:
        uri = os.getenv("MONGO_URI")
        _client = MongoClient(uri)
        
    # 'saapp_database' will be the name of your DB in the cluster
    return _client['saapp_database']

def test_connection():
    """Run this once to see if it works!"""
    db = get_db()
    if db is None:
        print("USE_DB is not set to true.")
        return False
    try:
        # The 'ping' command
        db.command('ping')
        print("Successfully connected to MongoDB!")
        return True
    except Exception as e:
        print(f"Connection failed: {e}")
        return False