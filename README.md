# FirePhenix Backend
## Introduction
This repository contains the flask backend for the gaming community website [Firephenix](firephenix.de).  
It implements a crash resistant TeamSpeak and Discord Bot, which track the time and other metrics of connected voice users.  
This enables a ranking system for users, in which they can advance to higher tiers, unlocking new benefits.
It also features a profile verification system, leveraging the [Steam API](https://developer.valvesoftware.com/wiki/Steam_Web_API),  
enabling users to merge both their Steam and Teamspeak accounts.
## Prerequisites
- Python 3.12.8
- MariaDB
- TeamSpeak Server (optional)

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

## Usage
Run the app with  
`flask run`  

For production, use tools like [Gunicorn](https://gunicorn.org/) to serve the app.

