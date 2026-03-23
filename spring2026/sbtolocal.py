import pandas as pd
import io
import os
from supabase import create_client, Client

# Load variables from local .env file without extra dependencies
def load_local_env(env_path: str = ".env") -> None:
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# 1. Initialize Supabase Client
load_local_env()
url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
key = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

if not url or not key:
    raise ValueError(
        "Missing Supabase credentials. Set NEXT_PUBLIC_SUPABASE_URL and "
        "NEXT_PUBLIC_SUPABASE_ANON_KEY in your environment or .env file."
    )

supabase: Client = create_client(url, key)

def fetch_and_unpack_data():
    print("Fetching records from Supabase...")
    
    # 2. Query the database (Note: Supabase limits to 1000 rows per request by default)
    # If you exceed 1000 records later, you will need to paginate this query
    response = supabase.table("exfiltrated_data").select("*").limit(1000).execute()
    records = response.data
    
    print(f"Successfully downloaded {len(records)} records.")
    
    all_rows = []
    
    # 3. Unpack the nested csv_dump strings
    print("Unpacking nested CSV data...")
    for record in records:
        # Extract metadata
        record_id = record.get("id")
        device_id = record.get("device_id")
        room_label = record.get("room_label")
        noise_type = record.get("noise_type")
        location = record.get("location")
        csv_string = record.get("csv_dump")
        
        if not csv_string:
            continue
            
        # Read the raw CSV string into a temporary Pandas DataFrame
        # io.StringIO makes Pandas treat the string as if it were a physical file
        try:
            temp_df = pd.read_csv(io.StringIO(csv_string))
            
            # Attach our database metadata as new columns to every row in this snapshot
            temp_df['db_id'] = record_id
            temp_df['device_id'] = device_id
            temp_df['room_label'] = room_label
            temp_df['noise_type'] = noise_type
            temp_df['location'] = location
            
            all_rows.append(temp_df)
        except Exception as e:
            print(f"Error parsing CSV for record ID {record_id}: {e}")
            
    # 4. Combine everything into one Master DataFrame
    if all_rows:
        master_df = pd.concat(all_rows, ignore_index=True)
        print(f"Master dataset created with {len(master_df)} total time-step rows.")
        
        # 5. Save locally for Machine Learning
        output_filename = "vr_sidechannel_master_dataset.csv"
        master_df.to_csv(output_filename, index=False)
        print(f"Data saved locally to: {output_filename}\n")
        
        return master_df
    else:
        print("No valid CSV data found.")
        return None

# Execute the pipeline
df = fetch_and_unpack_data()

# Display a preview of the clean data
if df is not None:
    print(df.head())