import json
import httpx
import os
import sys

def main():
    url = os.getenv("MOE_API_URL", "http://localhost:8002/v1/chat/completions")
    token = os.getenv("SYSTEM_API_KEY", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "moe-auto",
        "messages": [
            {"role": "user", "content": "Erstelle einen Webbasierten Fraktal Mandelbrotgenerator den man im Browser laufen lassen kann"}
        ]
    }
    
    print("Sending request to MoE API...", flush=True)
    try:
        with httpx.Client(timeout=300.0) as client:
            response = client.post(url, json=payload, headers=headers)
            print(f"Status Code: {response.status_code}", flush=True)
            if response.status_code == 200:
                print("Response received successfully!", flush=True)
                # print(response.json())
            else:
                print(f"Error: {response.text}", flush=True)
    except Exception as e:
        print(f"Exception occurred: {e}", flush=True)

if __name__ == "__main__":
    main()
