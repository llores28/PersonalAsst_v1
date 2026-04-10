#!/usr/bin/env python3
"""
Setup OAuth with ngrok for public redirect URI
This creates a public URL that forwards to localhost for OAuth callbacks
"""

import subprocess
import sys
import time
import requests
from pathlib import Path

def install_ngrok():
    """Check if ngrok is installed, if not install it"""
    try:
        result = subprocess.run(['ngrok', 'version'], capture_output=True, text=True)
        print(f"ngrok already installed: {result.stdout.strip()}")
        return True
    except FileNotFoundError:
        print("ngrok not found. Installing...")
        # For Windows
        if sys.platform == "win32":
            print("Please download ngrok from https://ngrok.com/download")
            print("Add it to your PATH or place it in the project root")
            return False
        # For Mac/Linux
        else:
            subprocess.run(['curl', '-s', 'https://ngrok-agent.s3.amazonaws.com/ngrok.asc', 
                          '|', 'sudo', 'tee', '/usr/local/bin/ngrok'], shell=True)
            subprocess.run(['sudo', 'chmod', '+x', '/usr/local/bin/ngrok'], shell=True)
            return True

def start_ngrok_tunnel():
    """Start ngrok tunnel for port 8083"""
    print("Starting ngrok tunnel for port 8083...")
    
    # Start ngrok in background
    proc = subprocess.Popen(
        ['ngrok', 'http', '8083', '--log=stdout'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Wait for ngrok to start
    time.sleep(3)
    
    # Get the public URL
    try:
        response = requests.get('http://127.0.0.1:4040/api/tunnels')
        tunnels = response.json()['tunnels']
        if tunnels:
            public_url = tunnels[0]['public_url']
            print(f"\nngrok tunnel established: {public_url}")
            print(f"\nAdd this redirect URI to Google Cloud Console:")
            print(f"{public_url}/oauth2callback")
            return public_url, proc
    except Exception as e:
        print(f"Error getting ngrok URL: {e}")
        proc.terminate()
        return None, None

def update_docker_compose(public_url):
    """Update docker-compose.yml to use the ngrok URL"""
    docker_compose_path = Path("docker-compose.yml")
    
    if not docker_compose_path.exists():
        print("docker-compose.yml not found!")
        return
    
    with open(docker_compose_path, 'r') as f:
        content = f.read()
    
    # Replace the redirect URI
    old_uri = "GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8083/oauth2callback"
    new_uri = f"GOOGLE_OAUTH_REDIRECT_URI={public_url}/oauth2callback"
    
    if old_uri in content:
        content = content.replace(old_uri, new_uri)
        
        with open(docker_compose_path, 'w') as f:
            f.write(content)
        
        print(f"\nUpdated docker-compose.yml with new redirect URI")
        print(f"Restart containers with: docker compose down && docker compose up -d")
    else:
        print(f"Could not find the redirect URI line in docker-compose.yml")

def main():
    print("=== OAuth Setup with ngrok ===\n")
    
    # Check/install ngrok
    if not install_ngrok():
        print("Please install ngrok manually and run this script again")
        return
    
    # Start ngrok tunnel
    public_url, proc = start_ngrok_tunnel()
    
    if not public_url:
        print("Failed to start ngrok tunnel")
        return
    
    print("\n" + "="*50)
    print("NEXT STEPS:")
    print("1. Add this URI to Google Cloud Console:")
    print(f"   {public_url}/oauth2callback")
    print("\n2. Update docker-compose.yml with this script? (y/n)")
    
    choice = input().strip().lower()
    if choice == 'y':
        update_docker_compose(public_url)
    
    print("\n3. After updating Google Cloud Console, press Ctrl+C to stop ngrok")
    
    try:
        # Keep ngrok running
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping ngrok...")
        proc.terminate()

if __name__ == "__main__":
    main()
