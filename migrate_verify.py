from pymongo import MongoClient

# Use your exact same URI
MONGO_URI = "mongodb+srv://saapp_db_user:R1$3ifyouwould!@saappcluster.m0lvlff.mongodb.net/?appName=SAAPPCluster"
client = MongoClient(MONGO_URI)

# 1. Print all databases to ensure we are looking at the right one
print("Databases found on this cluster:", client.list_database_names())

# 2. Iterate through databases to find where the documents are
for db_name in client.list_database_names():
    db = client[db_name]
    collections = db.list_collection_names()
    print(f"Database '{db_name}' contains collections: {collections}")
    
    # Check if 'documents' is in any of them
    if "documents" in collections:
        count = db["documents"].count_documents({})
        print(f"!!! FOUND IT: Database '{db_name}' has a 'documents' collection with {count} items.")