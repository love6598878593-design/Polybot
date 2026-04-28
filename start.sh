#!/bin/bash
gunicorn pm_bot:app --bind 0.0.0.0:8080 --timeout 120 --worker-class sync --threads 2
