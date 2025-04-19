# FirePhenix Backend
## Introduction
This repository contains the flask backend for the gaming community website [Firephenix](firephenix.de).  
It implements a robust TeamSpeak and Discord Bot, which track the time and other metrics of connected voice users.  
This enables a ranking system for users, in which they can advance to higher tiers, unlocking new benefits.
It also features a profile verification system, leveraging the [Steam API](https://developer.valvesoftware.com/wiki/Steam_Web_API),
enabling users to merge both their Discord and Teamspeak accounts.
## Prerequisites
- Python 3.12.8
- MariaDB
- Valkey

## Installation 
Install dependencies with  
`pip install -r requirements.txt`  

Create a .env file with:
```
DISCORD_TOKEN= # Your Discord bot API token
TS3_PASSWORD= # Your TeamSpeak 3 query password
DB_PASSWORD= # Your MariaDB password
SECRET_KEY= # Your randomized cookie secret key
```

## Configuration
The app is configured through the Config class in `app/config.py`.
Values like the database name, discord server id, teamspeak ports and more are configured here.

## Usage
Run your MariaDB & Valkey
Run the rankingsystem with
`bot_runner (start/stop/status)`
Run the Website API with  
`flask run`  

For production, use tools like [Gunicorn](https://gunicorn.org/) to serve the app.

