import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(url, key)

try:
    # Tables check karne ke liye ek simple query
    response = supabase.table("users").select("*").execute()
    print("Successfully connected to Supabase! Response:", response)
except Exception as e:
    print("Connection failed:", e)