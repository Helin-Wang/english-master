#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "Installing dependencies..."
pip3 install -r requirements.txt -q
python3 app.py
