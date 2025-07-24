#!/bin/bash

# Update the package list and install the font packages required by the PDF library
apt-get update
apt-get install -y fonts-freefont-ttf

# This command starts the Gunicorn server that runs the Flask app
gunicorn --bind=0.0.0.0 --timeout 600 app:app